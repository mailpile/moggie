# This is a fake SOP implementation, that instead of encrypting or signing,
# emits annotated plaintext showing what would be signed or encrypted.


class DemoStatelessOpenPGPClient:
    def list_profiles(self, subcommand='generate-key'):
        return {}

    def sign(self, data, *args, **kwargs):
        """
        Sign the data using the provided keys. Returns (micalg, signature).
        Input key material and the outputted signature should/will be ascii
        armored.
        """
        signature = b"""\
-----BEGIN PGP SIGNATURE-----

FAKE_SIGNATURE_DATA
-----END PGP SIGNATURE-----"""

        return signature, 'fake'

    def verify(self, data, *args, **kwargs):
        """
        Returns (bool, details) explaining whether the given signatures
        and certificates match the, data and the signatures fall within the
        window of time defined by the not_before and not_after parameters
        (if specified).
        """
        raise Exception('Unimplemented')


    def encrypt(self, data, *args, **kwargs):
        return (
            b'-----BEGIN PGP MESSAGE-----\n\n' +
            b''.join((b'E ' + line) for line in data.splitlines(True)) +
            b'\n-----END PGP MESSAGE-----\n')

    def decrypt(self, data, *args, **kwargs):
        return (
            b''.join(l[2:] for l in data.splitines(True) if l[:2] == b'E '),
            [],
            None)

