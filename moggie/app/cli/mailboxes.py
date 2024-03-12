# These are CLI commands for adding/removing/syncing mailbox contents.
#
#    moggie copy <search-terms> /path/to/mailbox
#    moggie remove <search-terms> /path/to/mailbox
#    moggie move <search-terms> /path/to/mailbox
#    moggie sync <search-terms> /path/to/mailbox
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
# TODO: Gathering sync-info from encrypted mailzips requires decrypting
#       which needs to be plumbed in. But also we don't want to have to do
#       all that work, write the short-circuit logic please thank you.
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
#          us, we shouldn't delete.
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
import time
import logging

from .command import Nonsense, CLICommand, AccessConfig
from .notmuch import CommandSearch

from moggie import get_shared_moggie
from moggie.api.requests import RequestTag, RequestDeleteEmails
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
        (None, None, 'common'),
        ('--context=',   ['default'], 'The context for scope and settings'),
        ('--q=',                  [], 'Search terms (used by web API)'),
        ('--qr=',                 [], 'Refining terms (used by web API)'),
        ('--or',             [False], 'Use OR instead of AND with search terms'),
        ('--offset=',          ['0'], 'Skip the first X results'),
        ('--limit=',            [''], 'Output at most X results'),
        ('--entire-thread=',      [], 'X=(true|false)'),
        ('--username=',       [None], 'Username with which to access email'),
        ('--password=',       [None], 'Password with which to access email'),
    ],[
        (None, None, 'strategies'),
        # If not, we ignore or tag as trash if we are indexed? Hmm.
        ('--delete',         [False], 'Allow sync/move to delete messages'),
        ('--trash',          [False], 'Tag messages as trash instead of delete'),
        ('--remove-after=',      [0], 'X=<hours>, delete X+ hours after copy'),
        ('--json-ui-state',       [], 'Include UI state in JSON result'),
    ],[
        (None, None, 'output'),
        ('--create=',         [None], 'X=(mailzip|maildir|mbox)'),
        ('--format=',       ['text'], 'X=(text*|text0|json|sexp)'),
        ('--output=',   ['metadata'], None),
        ('--zip-password=',   [None], 'Password for encrypted ZIP exports')]]

    def __init__(self, *args, **kwargs):
        self.moggie = get_shared_moggie()
        self.remove_after = None
        self.remove_messages = None
        self.remove_tag_op = '+trash'
        self.remove_post = []
        self.email_cache = {}
        self.target_mailbox = None
        self.target_sync_info = None
        self.source_sync_info = None
        self.email_emitter = None
        super().__init__(*args, **kwargs)

    def validate_configuration(self):
        super().validate_configuration(zip_encryption=False)

    def configure(self, args):
        args = list(args)

        self.target_mailbox = args.pop(-1)
        if args and args[-1] == '--':
            args.pop(-1)

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
            self.options['--create='] = ['maildir']
        elif os.path.exists(self.export_to):
            self.options['--create='] = [
                _format_from_path(self.export_to, exists=True)]
        elif not self.options['--create='][-1]:
            self.options['--create='] = [_format_from_path(self.export_to)]

        self.email_emitter = self.get_emitter(fmt=self.options['--create='][-1])
        self.options['--part='] = [0]  # Fetch entire emails
        self.remove_after = int(self.options['--remove-after='][-1])

        if self.sync_src:
            if self.options['--trash'][-1]:
                self.remove_messages = self.remove_by_tagging
            elif self.sync_src.startswith('mailbox:'):
                if self.options['--delete'][-1]:
                    self.remove_messages = self.remove_from_mailbox
            elif self.sync_src.startswith('in:'):
                self.remove_messages = self.remove_by_tagging
                self.remove_tag_op = '-' + self.sync_src.split()[0][3:]
            else:
                self.remove_messages = self.remove_by_tagging

        # If our source is a mailbox, we actually delete
        # If our source starts with a tag, we untag only that first tag
        # ... unless real deletion is requested, then tag +trash
        # If our source is anything more complicated, bail out

        return args

    async def _get_sync_info(self, mailbox, reverse=False):
        src, dst = self.sync_src, mailbox
        if reverse:
           dst, src = src, dst
           mailbox_username = self.options['--username='][-1]
           mailbox_password = self.options['--password='][-1]
        else:
           mailbox_username = None
           mailbox_password = self.options['--zip-password='][-1]

        with self.moggie as moggie:
            moggie.connect(autostart=False)  # FIXME: Should already be done!

            # FIXME: Need to finish the moggie.Moggie / cli.Commmand refactor

            sync_info = list(
                await moggie.set_mode(moggie.MODE_PY).async_search(
                    'mailbox:' + mailbox,
                    sync_src=src,
                    sync_dest=dst,
                    username=mailbox_username,
                    password=mailbox_password,
                    output='sync-info'))

            # FIXME: We have a weird inconsistency on how this gets returned,
            #        not really sure why.
            if sync_info and isinstance(sync_info[0], list):
                sync_info = sync_info[0]

            return sync_info

    async def get_target_sync_info(self):
        return await self._get_sync_info(self.target_mailbox)

    async def get_source_sync_info(self):
        return None

    async def remove_by_tagging(self, metadata_list):
        msg_idxs = sorted(list(set(m.idx for m in metadata_list if m)))
        if msg_idxs:
            msg_idx_list = 'id:' + ','.join('%s' % idx for idx in msg_idxs)
            query = RequestTag(
                context=self.context,
                tag_ops=[([self.remove_tag_op], msg_idx_list)],
                username=self.options['--username='][-1],
                password=self.options['--password='][-1])
            logging.debug('Tagging: %s %s' % (self.remove_tag_op, msg_idx_list))
            await self.worker.async_api_request(self.access, query)
        else:
            logging.debug('Nothing to remove')

    async def remove_from_mailbox(self, metadata_list):

        # Once we know what our sync-target-info is, we should pass
        # IDs back to the search to cut down on result size.

        if metadata_list:
            mailbox = self.sync_src[8:]  # len('mailbox:') == 8
            query = RequestDeleteEmails(
                context=self.context,
                from_mailboxes=[mailbox],
                metadata_list=metadata_list,
                username=self.options['--username='][-1],
                password=self.options['--password='][-1])
            logging.debug('Deleting %d messages from %s' % (len(metadata_list), mailbox))
            await self.worker.async_api_request(self.access, query)
        else:
            logging.debug('Nothing to remove')

    async def plan_actions(self, results, sync_info):
        # This is where deciding what to copy/delete/sync/etc. will happen
        raise RuntimeError('Please implement plan_actions')

    def want_pre_delete(self, plan):
        return []

    async def perform_query(self, *args):
      try:
        self.source_sync_info = await self.get_source_sync_info()
        self.target_sync_info = await self.get_target_sync_info()
        plan = await self.plan_actions(await super().perform_query(*args))
        self.remove_post.extend(self.want_pre_delete(plan))
        return plan
      except Exception as e:
        logging.exception('Asploded in perform_query()')
        raise

    async def run(self):
        try:
            rv = await super().run()
            if self.remove_messages is not None:
                await self.remove_messages(self.remove_post)
        except:
            logging.exception('remove_messages() failed')
        return rv


class CommandCopy(CommandMailboxes):
    """moggie copy <search-terms ...> /path/to/mailbox

    Copy messages matching the search, to the named mailbox. Messages
    already present in the mailbox will be omitted.
    """
    NAME = 'copy'
    WEB_EXPOSE = True
    ROLES = AccessConfig.GRANT_READ

    COPY_MESSAGE = 'copied'
    SKIP_MESSAGE = 'skipped'
    FAIL_MESSAGE = 'failed'

    RESULT_KEY  = 'COPY'
    FMT_COPIED  = 'Copied\t %(uuid)s\t%(subject)s'
    FMT_SKIPPED = 'Skipped\t%(uuid)s\t%(subject)s'
    FMT_FAILED  = 'Failed\t%(uuid)s\t%(subject)s'

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
        plan = []
        existing_uuids = set(si['uuid'] for si in self.target_sync_info)

        for result in results:
            md = Metadata(*result)
            want_copy = (md.uuid_asc not in existing_uuids)
            plan.append(
                (self.COPY_MESSAGE if want_copy else self.SKIP_MESSAGE, md))

        return plan

    async def as_exported_emails(self, pnm):
        if not pnm:
            return

        if not isinstance(pnm, tuple) and not isinstance(pnm[1], Metadata):
            logging.debug('Not (plan, metadata): %s' % pnm)
            return

        plan, metadata = pnm

        fmt, status = self.FMT_FAILED, self.FAIL_MESSAGE
        if plan == self.COPY_MESSAGE:
             emails_fmt = self.options['--create='][-1]
             async for (_, data) in self.as_emails(metadata, fmt=emails_fmt):
                if data:
                    await self.email_emitter((plan, data), last=False)
                    fmt, status = self.FMT_COPIED, plan
        else:
            fmt, status = self.FMT_SKIPPED, plan

        parsed = metadata.parsed()
        parsed['_metadata'] = metadata
        parsed['subject'] = parsed.get('subject', '(unknown)')
        parsed[self.RESULT_KEY] = status

        yield (fmt, parsed)


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
    FMT_DELETED = 'Deleted\t%(uuid)s\t%(subject)s'
    DELT_MESSAGE = 'deleted'

    def configure(self, args):
        rv = super().configure(args)
        if not self.remove_after:
            if self.remove_messages is not None:
                self.email_emitter = self.wrap_email_emitter(self.email_emitter)
            else:
                raise Nonsense('Unable to remove messages, check options')
        return rv

    def wrap_email_emitter(self, emitter):
        async def _removing_emitter(item, first=False, last=False):
            result = await emitter(item, first=first, last=last)
            if item is not None:
                self.remove_post.append(item[1]['_metadata'])
            return result
        return _removing_emitter

    def want_pre_delete(self, plan):
        for pnm in plan:
            if pnm:
                plan, metadata = pnm
                if plan == self.DELT_MESSAGE:
                    yield metadata

    async def plan_actions(self, results):
        plan = []
        existing_uuids = dict((si['uuid'], si) for si in self.target_sync_info)

        min_age = int(self.remove_after) # FIXME:  * 3600
        delete_ts = int(time.time()) - min_age

        for result in results:
            md = Metadata(*result)
            sync_info = existing_uuids.get(md.uuid_asc)

            if not sync_info:
                # Message is new, hasn't been seen before
                plan.append((self.COPY_MESSAGE, md))

            else:
                sync_info_info = sync_info.get('sync_info', {})
                if sync_info_info.get('sync'):
                    # We have sync-info, and it matches our src->dst, so we
                    # know when this message was copied over.
                    copied_ts = sync_info_info.get('ts')
                else:
                    # Sync info missing or doesn't match.
                    copied_ts = None

                if copied_ts and (copied_ts <= delete_ts):
                    plan.append((self.DELT_MESSAGE, md))
                else:
                    plan.append((self.SKIP_MESSAGE, md))

#       plan.append(False)  # EOM marker
        return plan

