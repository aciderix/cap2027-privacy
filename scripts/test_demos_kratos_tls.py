"""Tests TLS sans identifiants ni accès réseau.

Exécution : python -m unittest discover -s scripts -p 'test_demos_kratos_tls.py'
"""
import os
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import patch

import demos_kratos_check as checker


class FTPTLSContextTests(unittest.TestCase):
    def test_requires_valid_certificate_and_hostname(self):
        context = checker.ftp_tls_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertFalse(context.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN)

    def test_keeps_system_roots_and_loads_intermediate(self):
        default = ssl.create_default_context()
        context = checker.ftp_tls_context()
        self.assertTrue(set(default.get_ca_certs(binary_form=True)).issubset(
            set(context.get_ca_certs(binary_form=True))))
        pem = (Path(checker.__file__).resolve().parent / 'certs/lets-encrypt-yr1-chain.pem').read_text()
        certificates = [part + '-----END CERTIFICATE-----\n'
                        for part in pem.split('-----END CERTIFICATE-----') if part.strip()]
        self.assertEqual(len(certificates), 2)
        for certificate in certificates:
            self.assertIn(ssl.PEM_cert_to_DER_cert(certificate.strip()), context.get_ca_certs(binary_form=True))

    def test_works_outside_repository_directory(self):
        previous = os.getcwd()
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.chdir(directory)
                self.assertTrue(checker.ftp_tls_context().check_hostname)
        finally:
            os.chdir(previous)

    def test_missing_certificate_fails_closed(self):
        with patch.object(checker, '__file__', '/nonexistent/scripts/demos_kratos_check.py'):
            with self.assertRaises(FileNotFoundError):
                checker.ftp_tls_context()


if __name__ == '__main__':
    unittest.main()
