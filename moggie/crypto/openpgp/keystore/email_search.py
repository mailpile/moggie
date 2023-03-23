# A module for managing storage and discovery of OpenPGP key material

from moggie.util import NotFoundError

from ..keystore import OpenPGPKeyStore


class EmailSearchKeyStore(OpenPGPKeyStore):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tag_namespace = self.resources.get('tag_namespace')
        self.metadata = self.resources.get('metadata')
        self.search = self.resources.get('search')
 
    def get_cert(self, fingerprint):
        if not (self.metadata and self.search):
            raise NotFoundError()
        raise NotFoundError()

    def find_certs(self, search_terms):
        if not (self.metadata and self.search):
            raise NotImplementedError()
        raise NotImplementedError()
        yield None

    def list_certs(self, search_terms):
        if not (self.metadata and self.search):
            raise NotImplementedError()
        raise NotImplementedError()
        yield None


if __name__ == '__main__':
    import os
    import asyncio

    async def _al(async_iterator):
        output = []
        async for item in async_iterator:
            output.append(item)
        return output

    async def tests():
        print('Tests passed OK')

    asyncio.run(tests())
