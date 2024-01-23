# These are CLI commands for adding/removing/syncing mailbox contents.
#
#    moggie copy <search-terms> -- /path/to/mailbox
#    moggie remove <search-terms> -- /path/to/mailbox 
#    moggie move <search-terms> -- /path/to/mailbox 
#    moggie sync <search-terms> -- /path/to/mailbox 
#
# Search-terms may themselves include mailboxes, which makes this into a
# tool for copying/moving/syncing mailboxes with each-other.
#
# Common algorithm:
#   - Search for input messages
#   - Scan target mailbox for messages
#   - Generate sets: (in-both, missing-from-targ, gone-from-targ, new-in-targ)
#   - Then do the needful
#
# Notes:
#    - TBD
#
from .command import Nonsense, CLICommand, AccessConfig
from .notmuch import CommandSearch


class CommandMailboxes(CommandSearch):
    """
    Shared logic for copy/remove/move/sync.
    """
    NAME = 'internal-mailbox-stuff'
    ROLES = AccessConfig.GRANT_FS
    WEBSOCKET = False
    WEB_EXPOSE = False
    OPTIONS = [[
        (None, None, 'search'),
        ('--context=',   ['default'], 'The context for scope and settings'),
        ('--q=',                  [], 'Search terms (used by web API)'),
        ('--qr=',                 [], 'Refining terms (used by web API)'),
        ('--or',             [False], 'Use OR instead of AND with search terms'),
        ('--offset=',          ['0'], 'Skip the first X results'),
        ('--limit=',            [''], 'Output at most X results'),
        ('--tabs',           [False], 'Separate with tabs instead of spaces'),
        ('--entire-thread=',      [], 'X=(true|false)'),
        ('--username=',       [None], 'Username with which to access email'),
        ('--password=',       [None], 'Password with which to access email'),
        ('--json-ui-state',       [], 'Include UI state in JSON result'),
    ],[
        (None, None, 'output'),
        ('--create=',         [None], 'X=(mailzip|maildir|mbox)'),
        ('--format=',       ['text'], 'X=(text*|text0|json|sexp)'),
        ('--output=',       ['metadata'], None),
        ('--zip-password=',   [None], 'Password for encrypted ZIP exports')]]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_mailbox = None

    def configure(self, args):
        args = list(args)

        self.target_mailbox = args.pop(-1)
        minuses = args.pop(-1)
        if minuses != '--':
            raise Nonsense('Invalid arguments')

        args = super().configure(args)
        # FIXME: Only allow our subset of the possible output formats
        return args

    # FIXME: Augment the run() function or something, to query the
    #        target mailbox and figure out what exists there already.

    async def perform_actions(self, results):
        # This is where the actual copy/delete/sync/etc. will happen
        raise RuntimeError('Please implement perform_actions')

    async def perform_query(self, *args):
        return await self.perform_actions(await super().perform_query(*args))


class CommandCopy(CommandMailboxes):
    """moggie copy <search-terms ...> /path/to/mailbox

    Copy messages matching the search, to the named mailbox. Messages
    already present in the mailbox will be omitted.
    """
    NAME = 'copy'
    WEB_EXPOSE = True
    ROLES = AccessConfig.GRANT_READ

    async def perform_actions(self, results):
        done = []
        for result in results:
            print('Should copy %s to %s' % (result, self.target_mailbox))
            done.append(result)
        return done
