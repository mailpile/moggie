# Tools for working with DKIM signatures

from dkim import DKIMException
from dkim.asyncsupport import DKIM, get_txt_async
import asyncio


# This is adapted from the dkimpy async verification method, but it lets
# us verify multiple signatures in one pass (which will hopefully be a
# little more efficient).
async def verify_all_async(count, message,
        logger=None, dnsfunc=None, minkey=1024, timeout=5, tlsrpt=False):
    results = []
    if not dnsfunc:
        dnsfunc=get_txt_async
    d = DKIM(message,logger=logger,minkey=minkey,timeout=timeout,tlsrpt=tlsrpt)
    for idx in range(0, count):
        try:
            results.append(await d.verify(idx=idx, dnsfunc=dnsfunc))
        except Exception as e:
            if logger is not None:
                logger.error("%s" % e)
            results.append(False)
    return results
