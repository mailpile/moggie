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
#   - Search for input messages (search --output='sync-info')
#   - Scan target mailbox for messages (search --output='sync-info')
#   - Generate sets: (in-both, missing-from-targ, gone-from-targ, new-in-targ)
#   - Then do the needful
#
# The --output='sync-info' search should return lists of tuples:
#   - Preferred message file path
#   - Moggie message ID, synthetic or otherwise
#   - Moggie Sync-ID, if known
#
# To make these things efficient, we then make sure that the sync-info
# search is as fast as possible for mailzip and remote IMAP mailboxes.
#
# .....
# For sync, do we care whether a message was synced exactly this path,
# or do we just care whether Moggie created the message or not?
#
#    - For the copy case, we don't care
#        - Being sloppy about the sync-IDs will let us quickly see
#          which messages already exist in our metadata, if we're that lucky.
#    - For the delete case, also don't care
#        - Same comment as above
#    - For move:
#        - We only want to delete source messages N hours after we copied
#          them. So we do care when the sync happened and when. If it wasn't
#          us, we don't delete.
#    - For sync:
#        - The algorithm wants to say we are allowed to delete messages we
#          created ourselves. So if we copy into a mailbox, but then ask
#          moggie to bring it into sync with a tag or search, deleting them
#          kinda does make sense? Otherwise we should just keep copying.
#        - Greedy sync makes sure all messages exist on both sides.
#        - Active sync makes sure message removals propagate
#        - ...
#
# Notes:
#    - TBD
#
import os

from .command import Nonsense, CLICommand, AccessConfig
from .notmuch import CommandSearch

from moggie import get_shared_moggie
from moggie.email.metadata import Metadata


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
        ('--entire-thread=',      [], 'X=(true|false)'),
        ('--username=',       [None], 'Username with which to access email'),
        ('--password=',       [None], 'Password with which to access email'),
        # If not, we ignore or tag as trash if we are indexed? Hmm.
        ('--allow-delete',   [False], 'Allow sync to delete messages'),
        ('--delete-after=',      [0], 'X=<hours>, delete X+ hours after copy'),
        ('--json-ui-state',       [], 'Include UI state in JSON result'),
    ],[
        (None, None, 'output'),
        ('--create=',         [None], 'X=(mailzip|maildir|mbox)'),
        ('--format=',       ['text'], 'X=(text*|text0|json|sexp)'),
        ('--output=',   ['metadata'], None),
        ('--zip-password=',   [None], 'Password for encrypted ZIP exports')]]

    def __init__(self, *args, **kwargs):
        self.moggie = get_shared_moggie()
        self.delete_after = None
        self.target_mailbox = None
        self.target_sync_info = None
        self.source_sync_info = None
        self.email_emitter = None
        super().__init__(*args, **kwargs)

    def configure(self, args):
        args = list(args)

        self.target_mailbox = args.pop(-1)
        minuses = args.pop(-1)
        if minuses != '--':
            raise Nonsense('Invalid arguments')

        args = super().configure(args)

        def _format_from_path(fn, exists=False):
            ext = fn.rsplit('.', 1)[-1]
            if ext in ('mdz', 'zip'):
                return 'mailzip'
            elif ext in ('mbox', 'mbx'):
                return 'mbox'
            elif ext in ('tar', 'tgz', 'bz2', 'xz'):
                if exists:
                    raise Nonsense('Cannot update existing archive: %s' % fn)
                return 'maildir'
            raise Nonsense('Unsure what mailbox format to use for %s' % fn)

        self.export_to = self.sync_dest = self.target_mailbox
        if os.path.isdir(self.export_to):
            self.options['--create='] = 'maildir'
        elif os.path.exists(self.export_to):
            self.options['--create='] = [
                _format_from_path(self.export_to, exists=True)]
        elif not self.options['--create='][-1]:
            self.options['--create='] = [_format_from_path(self.export_to)]

        self.email_emitter = self.get_emitter(fmt=self.options['--create='][-1])
        self.options['--part='] = [0]  # Fetch entire emails
        self.delete_after = int(self.options['--delete-after='][-1])

        return args

    # FIXME: Augment the run() function or something, to query the
    #        target mailbox and figure out what exists there already.

    async def get_target_sync_info(self):
        with self.moggie as moggie:
            moggie.connect(autostart=False)  # FIXME: Should already be done!

            # FIXME: Need to finish the moggie.Moggie / cli.Commmand refactor

            sync_info = list(
                await moggie.set_mode(moggie.MODE_PY).async_search(
                    'mailbox:' + self.target_mailbox,
                    sync_src=self.sync_src,
                    sync_dest=self.target_mailbox,
                    output='sync-info'))

            # FIXME: We have a weird inconsistency on how this gets returned,
            #        not really sure why.
            if sync_info and isinstance(sync_info[0], list):
                sync_info = sync_info[0]

            return sync_info

    async def get_source_sync_info(self):
        pass

    async def plan_actions(self, results, sync_info):
        # This is where the actual copy/delete/sync/etc. will happen
        raise RuntimeError('Please implement perform_actions')

    async def perform_query(self, *args):
      try:
        self.source_sync_info = await self.get_source_sync_info()
        self.target_sync_info = await self.get_target_sync_info()
        return await self.plan_actions(await super().perform_query(*args))
      except:
        logging.exception('Asploded in perform_query()')
        raise


class CommandCopy(CommandMailboxes):
    """moggie copy <search-terms ...> /path/to/mailbox

    Copy messages matching the search, to the named mailbox. Messages
    already present in the mailbox will be omitted.
    """
    NAME = 'copy'
    WEB_EXPOSE = True
    ROLES = AccessConfig.GRANT_READ

    RESULT_KEY = 'COPY'
    FMT_C = 'Copied\t %(uuid)s\t%(subject)s'
    FMT_S = 'Skipped\t%(uuid)s\t%(subject)s'

    async def as_exported_emails(self, md):
        if not md:
            return

        elif isinstance(md, tuple):
            yield (self.FMT_S, md[1])

        else:
            emails_fmt = self.options['--create='][-1]
            async for (_, data) in self.as_emails(md, fmt=emails_fmt):
                if data:
                    raw_message = data['_data']
                    await self.email_emitter((None, data), last=False)

                    parsed = Metadata(*md).parsed()
                    parsed[self.RESULT_KEY] = 'wanted %d bytes' % len(raw_message)
                    yield (self.FMT_C, parsed)
                else:
                    parsed = Metadata(*md).parsed()
                    parsed[self.RESULT_KEY] = 'not found'
                    yield (self.FMT_S, parsed)

    def get_emitter(self, fmt=None):
        emitter = super().get_emitter(fmt=fmt)
        if fmt is not None:
            return emitter

        async def wrapped_emitter(item, first=False, last=False):
            result = await emitter(item, first=first, last=last) 
            if last and self.email_emitter:
                await self.email_emitter(None, last=True)
            return result

        return wrapped_emitter

    def get_formatter(self):
        self.write_error = lambda e: None
        return self.as_exported_emails

    async def plan_actions(self, results):
        done = []
        existing_uuids = set(si['uuid'] for si in self.target_sync_info)

        for result in results:
            md = Metadata(*result)
            want_copy = (md.uuid_asc not in existing_uuids)
            if want_copy:
                done.append(result)
            else:
                parsed = md.parsed()
                parsed[self.RESULT_KEY] = 'skipped'
                done.append(('skipped', parsed))

        done.append(False)
        return done


class CommandMove(CommandCopy):
    """moggie move <search-terms ...> /path/to/mailbox

    Copy messages matching the search, to the named mailbox. Messages
    already present in the mailbox will be omitted. Once messages have
    been copied, originals will be deleted if enough time has passed.
    """
    NAME = 'move'
    WEB_EXPOSE = True
    ROLES = AccessConfig.GRANT_READ

    RESULT_KEY = 'MOVE'
    FMT_D = 'Deleted\t%(uuid)s\t%(subject)s'

    # FIXME: After the copy, check which messages we want to delete,
    #        based on what just happened + what messages expired?
    # Or maybe we delete first any expired, and then delete as we go
    # if the delay is zero?  Seems like the latter is less duplicate
    # code
