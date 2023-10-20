import logging

from ...api.requests import *
from ...config import AppConfig, AccessConfig
from .command import Nonsense, CLICommand


class CommandAutotag(CLICommand):
    """moggie autotag <op> <tag1> [<tag2> ...] -- <search terms>

    Run the autotagger(s) for the named tags, against the messages
    matching the search terms. The default junk/trash suppression will
    be disabled for the search.

    ### Options

    %(OPTIONS)s

    See also `moggie help autotag-train` for information on how to train
    the classifier and `moggie help autotag-classify` for manual testing.
    """
    NAME = 'autotag'
    ROLES = (AccessConfig.GRANT_TAG_RW,)
    WEBSOCKET = False
    WEB_EXPOSE = True
    AUTO_START = False
    OPTIONS = [[
        ('--format=',     ['text'], 'X=(text*|json)'),
        ('--context=', ['default'], 'X=<ctx>, work within a specific context')]]

    def validate(self):
        if not self.tags:
            raise Nonsense('At least one tag is required.')

    def configure(self, args):
        try:
            dashes = args.index('--')
            args, self.terms = args[:dashes], args[dashes+1:]
        except ValueError:
            self.terms = []
        self.tags = self.strip_options(args)
        for i, t in enumerate(self.tags):
            if t.startswith('tag:'):
                self.tags[i] = 'in:%s' % t[4:]
            elif not t.startswith('in:'):
                self.tags[i] = 'in:%s' % t
        self.validate()
        return []

    async def perform_search(self, worker, ctx):
        if self.terms:
            from moggie.search.filters import AutoTagger
            return await worker.async_api_request(self.access,
                AutoTagger.MakeSearchObject(context=ctx, terms=self.terms))
        else:
            return None

    def print_result_text(self, result):
        self.print('%s' % '\n'.join('%s' % r for r in result))

    def print_result(self, result):
        fmt = self.options['--format='][-1]
        if fmt == 'json':
            self.print_json(result)
        else:
            self.print_result_text(result)

    async def run(self):
        ctx = self.get_context()
        search_res = await self.perform_search(self.worker, ctx)
        atag_res = await self.worker.async_api_request(self.access,
            RequestAutotag(context=ctx, tags=self.tags, search=search_res))
        self.print_result(atag_res)


class CommandAutotagTrain(CommandAutotag):
    """moggie autotag-train [<tag1> [<tag2> ...]] -- <search terms>

    Train the autotagger(s) for the named tags.

    ### Options

    %(OPTIONS)s

    Training will take place using the messages matching the search terms.
    Messages that match the search AND are already tagged, will be treated
    as positive matches (similar mail should be tagged; "spam"), messages
    that match the search but are not tagged, will be treated as examples
    of what should not be tagged (negative matches; "ham"). The usual
    suppression of junk/trash results will be disabled.

    If no search terms are given, then for tags which have auto-training
    enabled, the training set will be chosen automatically. In this case,
    for tags without auto-training enabled, nothing will be done.

    If no tags are given, all configured autotaggers will be trained,
    either within the specified context or globally.

    ### Scheduling and Configuration

    This command is included in moggie's default periodic scheduler, with
    training taking place every 15 minutes, and compacting once per week.
    Note that this only effects tags with auto-training enabled.

    Configuration (thresholds etc.) and the training weights themselves
    are stored in JSON files with the suffix `.atag`, in the user filter
    directory.

    See also: `$MOGGIE_HOME/crontab`, `$MOGGIE_HOME/filters`
    """
    NAME = 'autotagtrain'
    ROLES = (
        AccessConfig.GRANT_TAG_X +
        AccessConfig.GRANT_TAG_RW)
    WEBSOCKET = False
    WEB_EXPOSE = True
    AUTO_START = False
    OPTIONS = [[
        ('--format=', ['text'], 'X=(text*|json)'),
        ('--context=',      [], 'X=<ctx>, work within a specific context'),
        ('--compact',  [False], 'Compact the weight database after training')]]

    def validate(self):
        pass

    async def run(self):
        contexts = []
        if self.options['--context=']:
            contexts = self.options['--context=']
        elif self.tags:
            self.options['--context='].append('default')
            contexts = [self.get_context()]
        else:
            ns_ctx = {}
            for ctx_id, ctx in self.get_all_contexts().items():
                if ctx.tag_namespace not in ns_ctx:
                    ns_ctx[ctx.tag_namespace] = ctx_id
            contexts = list(ns_ctx.values())
            logging.debug('Training Autotaggers in contexts: %s' % contexts)

        results = []
        for ctx in contexts:
            sres = await self.perform_search(self.worker, ctx)
            treq = RequestAutotagTrain(
                context=ctx, tags=self.tags, search=sres,
                compact=self.options['--compact'][-1])
            tres = await self.worker.async_api_request(self.access, treq)
            results.append(tres)

        self.print_result(results)


class CommandAutotagClassify(CommandAutotag):
    """moggie autotagclassify <tag1> [<tag2> ...] -- <keywords...>

    Run the autotagger(s) for the named tags, against the keywords
    provided, returning a breakdown of scores for each tag. If no tags
    are specified, test against all available autotaggers.

    ### Options

    %(OPTIONS)s

    See also `moggie help autotagtrain` for information on how to train
    the classifier.
    """
    NAME = 'autotag-classify'
    ROLES = (AccessConfig.GRANT_TAG_RW,)
    WEBSOCKET = False
    WEB_EXPOSE = True
    AUTO_START = False
    OPTIONS = [[
        ('--format=',     ['text'], 'X=(text*|json)'),
        ('--context=', ['default'], 'X=<ctx>, work within a specific context')]]

    def validate(self):
        if not self.terms:
            raise Nonsense('At least one keyword is required.')

    async def run(self):
        self.print_result(await self.worker.async_api_request(self.access,
            RequestAutotagClassify(
                context=self.get_context(),
                tags=self.tags,
                keywords=self.terms)))

