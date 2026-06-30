import unittest
from ai_proctoring_system.app import app

class ProctoringTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True

    def test_login_and_dashboard(self):
        # 1. Access Index
        response = self.app.get('/')
        self.assertEqual(response.status_code, 200)

        # 2. Login Student
        response = self.app.post('/login/student', data={
            'name': 'Test Student',
            'regno': '12345',
            'email': 'student@test.com',
            'subject': 'Physics'
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Exam Dashboard', response.data)
        print("Login and dashboard test succeeded!")

if __name__ == '__main__':
    unittest.main()
