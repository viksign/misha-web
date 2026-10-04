import logging
from datetime import timedelta
from ipaddress import ip_address

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError
from django.http import HttpResponse
from django.utils import timezone
from redis.exceptions import RedisError

from .models import BlockedIP, SecurityEvent


logger = logging.getLogger(__name__)


class SuspiciousActivityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        client_ip = self._client_ip(request)
        if not client_ip:
            return self.get_response(request)

        now = timezone.now()
        try:
            blocked_until = self._blocked_until(client_ip, now)
            if blocked_until and blocked_until > now.timestamp():
                return HttpResponse('Access temporarily restricted.', status=403)

            request_window = int(getattr(settings, 'SECURITY_REQUEST_WINDOW_SECONDS', 60))
            request_period = int(now.timestamp()) // request_window
            request_count = self._increment(
                f'security:requests:{client_ip}:{request_period}',
                request_window * 2,
            )
            request_limit = int(getattr(settings, 'SECURITY_REQUEST_LIMIT', 300))
            if request_count > request_limit:
                self._block(
                    client_ip,
                    request.path,
                    f'More than {request_limit} requests in {request_window} seconds',
                    request_count,
                    int(getattr(settings, 'SECURITY_REQUEST_BLOCK_SECONDS', 900)),
                    now,
                )
                return HttpResponse('Request limit exceeded. Try again later.', status=429)

            if request.method == 'POST' and request.path.rstrip('/') in {
                settings.LOGIN_URL.rstrip('/'), '/admin/login', '/staff-auth/login', '/staff-auth/setup',
            }:
                login_window = int(getattr(settings, 'SECURITY_LOGIN_WINDOW_SECONDS', 600))
                login_period = int(now.timestamp()) // login_window
                login_count = self._increment(
                    f'security:login:{client_ip}:{login_period}',
                    login_window * 2,
                )
                login_limit = int(getattr(settings, 'SECURITY_LOGIN_LIMIT', 12))
                if login_count > login_limit:
                    self._block(
                        client_ip,
                        request.path,
                        f'More than {login_limit} login submissions in {login_window} seconds',
                        login_count,
                        int(getattr(settings, 'SECURITY_LOGIN_BLOCK_SECONDS', 1800)),
                        now,
                    )
                    return HttpResponse('Too many login attempts. Try again later.', status=429)
        except RedisError:
            logger.error('Redis unavailable for request security controls; nginx rate limits remain active.')

        return self.get_response(request)

    @staticmethod
    def _client_ip(request):
        value = request.META.get('HTTP_X_REAL_IP') or request.META.get('REMOTE_ADDR')
        try:
            address = ip_address(value)
        except (TypeError, ValueError):
            return None
        return str(address) if address.is_global else None

    @staticmethod
    def _increment(key, timeout):
        if cache.add(key, 1, timeout=timeout):
            return 1
        try:
            return cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=timeout)
            return 1

    @staticmethod
    def _blocked_until(client_ip, now):
        key = f'security:blocked:{client_ip}'
        cached = cache.get(key)
        if cached is not None:
            return float(cached) if cached else None

        block = BlockedIP.objects.filter(ip_address=client_ip, blocked_until__gt=now).first()
        if block:
            expires_at = block.blocked_until.timestamp()
            cache.set(key, expires_at, timeout=max(1, int((block.blocked_until - now).total_seconds())))
            return expires_at

        cache.set(key, 0, timeout=30)
        return None

    @staticmethod
    def _block(client_ip, path, reason, request_count, duration, now):
        lock_key = f'security:block-lock:{client_ip}'
        if not cache.add(lock_key, 1, timeout=duration):
            return

        blocked_until = now + timedelta(seconds=duration)
        try:
            BlockedIP.objects.update_or_create(
                ip_address=client_ip,
                defaults={'reason': reason, 'blocked_until': blocked_until},
            )
            SecurityEvent.objects.create(
                ip_address=client_ip,
                event_type='auto_block',
                path=path[:500],
                reason=reason,
                request_count=request_count,
                blocked_until=blocked_until,
            )
            retention_days = int(getattr(settings, 'SECURITY_EVENT_RETENTION_DAYS', 90))
            SecurityEvent.objects.filter(created_at__lt=now - timedelta(days=retention_days)).delete()
            BlockedIP.objects.filter(blocked_until__lt=now - timedelta(days=retention_days)).delete()
        except DatabaseError:
            logger.exception('Unable to persist automatic IP block for %s', client_ip)

        cache.set(f'security:blocked:{client_ip}', blocked_until.timestamp(), timeout=duration)