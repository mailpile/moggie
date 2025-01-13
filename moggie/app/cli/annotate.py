import asyncio
import json
import logging
import shlex
import sys

from ...api.requests import *
from ...email.metadata import Metadata
from .command import Nonsense, CLICommand
from .notmuch import CommandSearch


class CommandAnnotate(CommandSearch):
    """moggie annotate [options] <terms> -- <key1>=<value1> .. <keyN>=<valN>

    Annotate messages matching the search terms. Annotations are stored
    verbatim in the message metadata.

    ### Examples

    ### Search Options

    %(search)s

    Note: See `moggie help search` for details.

    ### Output Options

    %(output)s

    """
    NAME = 'annotate'
    OPTIONS = [[
        (None, None, 'search'),
        ('--context=',   ['default'], 'The context for scope and settings'),
        ('--q=',                  [], 'Search terms (used by web API)'),
        ('--qr=',                 [], 'Refining terms (used by web API)'),
        ('--or',             [False], 'Use OR instead of AND with search terms'),
        ('--offset=',          ['0'], 'Skip the first X results'),
        ('--limit=',            [''], 'Output at most X results'),
        ('--tabs',           [False], 'Separate with tabs instead of spaces'),
        ('--username=',       [None], ''),  # Needed to keep CommandSearch happy
        ('--password=',       [None], ''),  # Needed to keep CommandSearch happy
        ('--json-ui-state',  [False], ''),
        ('--entire-thread=', ['false'], 'X=(true|false*) Annotate all in thread'),
    ],[
        (None, None, 'output'),
        ('--format=',       ['text'], 'X=(text*|text0|json|sexp)'),
        ('--output=',    ['default'], 'X=(summary*|messages|metadata)'),
    ]]

    def __init__(self, *args, **kwargs):
        self.annotations = {}
        self.to_metadata = None
        super().__init__(*args, **kwargs)

    def configure(self, args):
        dashes = args.index('--') if ('--' in args) else None

        if dashes is not None:
            key_value_pairs = args[dashes+1:]
            for pair in key_value_pairs:
                if pair[:1] == '=':
                    pair = pair[1:]
                key, value = pair.split('=', 1)
                self.annotations['=' + key.strip()] = value.strip()

        args = super().configure(args[:dashes])

        frmat = self.options['--format='][-1]
        if frmat not in ('text', 'text0', 'json', 'sexp'):
            raise Nonsense('Invalid format: %s' % frmat)

        output = self.options['--output='][-1]
        if output == 'default':
            output = self.options['--output='][-1] = 'summary'
        elif output not in ('summary', 'messages', 'metadata'):
            raise Nonsense('Invalid output: %s' % output)

        full_thread = (self.options['--entire-thread='][-1] != 'false')
        if output == 'metadata':
            self.to_metadata = self._md_from_md
        elif output == 'messages':
            self.to_metadata = self._md_from_md
        elif output == 'summary':
            if full_thread:
                self.to_metadata = self._md_from_summary
            else:
                self.to_metadata = self._md_from_md
        else:
            raise Nonsense('Unsure how to interpret results')

        return args

    def _md_from_md(self, result):
        return [Metadata(*result)]

    def _md_from_summary(self, result):
        return [Metadata(*r) for r in result['messages']]

    async def annotate_messages(self, idxs, annotations):
        aReq = RequestAnnotate(
            context=self.context,
            terms=' OR '.join('id:%s' % i for i in idxs),
            annotations=annotations)
        return await self.worker.async_api_request(self.access, aReq)

    async def act_on_results(self, results):
        """
        Attempt to annotate a set of metadata results; return those that were
        successfully annotated.
        """
        mdlist_result_pairs = [
            (self.to_metadata(result), result) for result in results]

        req_idxs = []
        for mdlist, result in mdlist_result_pairs:
            req_idxs.extend(md.idx for md in mdlist)

        aRes = await self.annotate_messages(req_idxs, self.annotations)

        failed = []
        if 'results' not in aRes:
            failed.extend(range(0, len(mdlist_result_pairs)))
        else:
            annotated = set(aRes['results'])
            for i, (mdlist, result) in enumerate(mdlist_result_pairs):
                matched = 0
                for md in mdlist:
                    if md.idx in annotated:
                        for k, v in self.annotations.items():
                            if v in ('', None):
                                if k in md.more:
                                    del md.more[k]
                            else:
                                md.more[k] = v
                        matched += 1
                if not matched:
                    failed.append(i)
                elif isinstance(result, dict):
                    result['messages'] = mdlist
                else:
                    result[:] = mdlist[0]

        return [p[1]
            for i, p in enumerate(mdlist_result_pairs) if i not in failed]

    async def perform_query(self, *args):
        """
        For each batch of results, attempt annotation and then augment results
        to reflect how that went.
        """
        try:
            results = await super().perform_query(*args)
            return await self.act_on_results(results)
        except Exception as e:
            logging.exception('Asploded in perform_query()')
            raise
