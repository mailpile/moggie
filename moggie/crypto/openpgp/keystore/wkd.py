# A module for managing storage and discovery of OpenPGP key material

from moggie.util import NotFoundError

from ..keystore import OpenPGPKeyStore


class WKDKeyStore(OpenPGPKeyStore):
    def find_certs(self, search_terms):
        raise NotImplementedError()
        yield None

    def list_certs(self, search_terms):
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
