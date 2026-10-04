from django.http import HttpResponse
from django.template.loader import get_template


def _error_response(status, title, message):
    content = get_template('shop/error.html').render({
        'status': status,
        'title': title,
        'message': message,
    })
    response = HttpResponse(content, status=status)
    response['Cache-Control'] = 'no-store'
    return response


def bad_request(request, exception):
    return _error_response(400, 'Unable to process your request', 'Please try again with a fresh page.')


def permission_denied(request, exception):
    return _error_response(403, 'Unable to complete this request', 'You may not have permission to access this page.')


def page_not_found(request, exception):
    return _error_response(404, 'Page not found', 'The page you are looking for may have moved or is no longer available.')


def server_error(request):
    return _error_response(500, 'Something went wrong', 'We are unable to complete your request right now. Please try again shortly.')


def csrf_failure(request, reason=''):
    return _error_response(403, 'Please refresh and try again', 'Your form could not be verified. Reload the page before submitting it again.')