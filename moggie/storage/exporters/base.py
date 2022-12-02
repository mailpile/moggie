from io import BytesIO


class ClosableBytesIO(BytesIO):
    """
    Work around the fact that BytesIO becomes unusable on close(), but
    we want to work with interfaces like zipfile that close their files
    when they finish.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._closed = False 

    def cleanup(self):
        super().close()

    def close(self):
        self._closed = True

    def dump(self):
        data = self.getvalue()
        self.cleanup()
        return data


class BaseExporter:
    def __init__(self, outfile, password=None):
        self.fd = outfile
        self.password = password

    def can_encrypt(self):
        return False

    def __enter__(self):
        return self

    def __exit__(self, *args, **kwargs):
        self.close()

    def close(self):
        self.fd.close()

    def transform(self, metadata, message):
        """Prepare the message for writing out to the archive."""
        return bytearray(message)

    def export(self, metadata, message):
        self.fd.write(self.transform(metadata, message))


if __name__ == '__main__':
    bio = ClosableBytesIO()

    with BaseExporter(bio) as exp:
        exp.export(None, b"""\
From: bre@example.org
To: bre@example.org
Subject: ohai

Hello world!
""")

    assert(bio.dump().startswith(b'From: bre'))

    print('Tests passed OK')
