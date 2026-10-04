from tempfile import TemporaryDirectory

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import CustomDesignRequest


class CustomDesignPhotoAccessTests(TestCase):
    def test_only_request_owner_and_staff_can_download_private_photo(self):
        owner = User.objects.create_user(username='photo-owner', password='test-password')
        other_user = User.objects.create_user(username='other-user', password='test-password')
        staff_user = User.objects.create_user(username='photo-staff', password='test-password', is_staff=True)

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            request = CustomDesignRequest.objects.create(
                user=owner,
                title='Private reference',
                product_details='A private customer design.',
                photo=SimpleUploadedFile('reference.jpg', b'private image bytes', content_type='image/jpeg'),
            )
            photo_url = reverse('custom_design_photo', args=[request.pk])

            anonymous_response = self.client.get(photo_url)
            self.assertEqual(anonymous_response.status_code, 302)

            self.client.force_login(other_user)
            unauthorized_response = self.client.get(photo_url)
            self.assertEqual(unauthorized_response.status_code, 404)

            self.client.force_login(owner)
            owner_response = self.client.get(photo_url)
            self.assertEqual(owner_response.status_code, 200)
            self.assertEqual(owner_response['Cache-Control'], 'private, no-store')
            self.assertIn('attachment;', owner_response['Content-Disposition'])
            self.assertEqual(b''.join(owner_response.streaming_content), b'private image bytes')

            self.client.force_login(staff_user)
            staff_response = self.client.get(photo_url)
            self.assertEqual(staff_response.status_code, 200)