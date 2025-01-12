import copy
import logging

from ..email.parsemime import parse_message as ep_parse_message
from ..util.mailpile import PleaseUnlockError
from ..util.dumbcode import *


def _u(txt):
    try:
        return txt if isinstance(txt, str) else str(txt, 'utf-8')
    except UnicodeDecodeError:
        return ('%s' % txt)


class MailboxStorageMixin:
    """
    This mixin relies on the target class implementing:
        - ask_secret, set_secret
        - get_mailbox
        - can_handle_ptr
        - __getitem__
    """
    def can_handle_metadata(self, metadata):
        for ptr in metadata.pointers:
            if self.can_handle_ptr(ptr):
                return True
        return False

    def unlock_mailbox(self, mailbox, username, password, context, sec_ttl):
        if hasattr(mailbox, 'unlock'):
            _unlock_kwa = {}
            if self.ask_secret:
                def _ak(resource):
                    return self.ask_secret(context, resource)
                _unlock_kwa['ask_key'] = _ak
            if self.set_secret:
                def _sk(resource, key):
                    self.set_secret(context, resource, key, sec_ttl)
                _unlock_kwa['set_key'] = _sk
            mailbox.unlock(username, password, **_unlock_kwa)
        return mailbox

    def iter_mailbox(self, key,
            skip=0, limit=None, ids=None, reverse=False, sync_id=None,
            username=None, password=None, context=None, secret_ttl=None,
            search_terms=None):
        parser = iter([])
        if (limit is None) or (limit > 0):
            mailbox = self.get_mailbox(key, auth=not (username or password))
            if mailbox is None:
                logging.debug('%s: Failed to open mailbox: %s' % (self, key))
            else:
                if username or password:
                    self.unlock_mailbox(
                        mailbox, username, password, context, secret_ttl)
                parser = mailbox.iter_email_metadata(
                    skip=skip, ids=ids, reverse=reverse, sync_id=sync_id)
                    # FIXME: Pass in search terms, so we can leverage
                    #        server-side searching. Local mailboxes should
                    #        implement some kind of grep functionality.

        if (limit is None) and (ids is None):
            yield from parser
            return

        if ids is not None:
            ids = copy.copy(ids)

        for msg in parser:
            if ids is not None:
                if not ids:
                    return
                matches = False
                for i in ids:
                    if mailbox.compare_idxs(i, msg.idx):
                        matches = True
                        ids.remove(i)
                        break
                if not matches:
                    continue

            yield msg
            if limit is not None:
                limit -= 1
                if limit <= 0:
                    break

    def message(self, metadata, with_ptr=False,
            username=None, password=None, context=None, secret_ttl=None):
        """
        Returns a slice of bytes that map to the message on disk.
        Works for both maildir and mbox messages.
        """
        if username or password:
            gi_args = (username, password, context, secret_ttl)
        else:
            gi_args = set()

        # FIXME: We need to check whether this is actually the right message,
        #        or whether the mailbox has changed from under us. If it has, we
        #        need to (in coordination with the metadata index) rescan for
        #        messages update the metadata. This is true for both mbox and
        #        Maildir: Maildir files may get renamed if other apps change
        #        read/unread status or assign tags. For mbox, messages can move
        #        around within the file.
        skipped = 0
        for ptr in metadata.pointers:
            if not self.can_handle_ptr(ptr):
                skipped += 1
                logging.info('Cannot handle PTR: %s' % ptr)
                continue
            try:
                if with_ptr:
                    return ptr, self.__getitem__(ptr.ptr_path, *gi_args)
                else:
                    return self.__getitem__(ptr.ptr_path, *gi_args)
            except PleaseUnlockError:
                raise
            except (KeyError, OSError, IOError) as e:
                logging.info('Loading e-mail failed: %s' % e)

        raise KeyError('Not found: %s' % _u(dumb_decode(ptr.ptr_path)))

    def parse_message(self, metadata, **kwargs):
        msg = self.message(metadata, **kwargs)
        return ep_parse_message(msg, fix_mbox_from=(msg[:5] == b'From '))

    def delete_message(self, metadata=None, ptrs=None):
        """
        Delete the message from one or more locations.
        Returns a list of pointers which could not be deleted.
        """
        failed = []
        for ptr in (ptrs if (ptrs is not None) else metadata.pointers):
            if self.can_handle_ptr(ptr):
                try:
                    del self[ptr.ptr_path]
                except (KeyError, OSError, IOError):
                    failed.append(ptr)
            else:
                failed.append(ptr)
        return failed

    def bulk_delete_messages(self, key, metadata_list,
            username=None, password=None, context=None, secret_ttl=None):
        """
        Try to delete all the messages described by the metadata in the list.

        Returns tuple of lists of metadata: (deleted, ignored, failed, moved)

        Note that the moved list may contain metadata not present in the
        original request, in the case that deleting this message caused
        others to change locations.
        """
        if isinstance(key, str):
            key = bytes(key, 'utf-8')
        if username or password:
            gi_args = (username, password, context, secret_ttl)
        else:
            gi_args = set()

        mailbox = self.get_mailbox(key, auth=not (username or password))
        if not mailbox:
            logging.debug('Failed to open mailbox: %s' % key)
            return ([], metadata_list, [], [])  # All ignored
        if username or password:
            self.unlock_mailbox(
                mailbox, username, password, context, secret_ttl)

        deleted = []
        ignored = []
        failed = []
        moved = []
        for md in metadata_list:
            all_ptrs = md.pointers
            loop_max = len(all_ptrs)
            deleted_c = missing_c = errored_c = 0
            for i in range(0, loop_max):
                ptr = all_ptrs.pop(0)
                if ptr.container != key:
                    logging.debug('Delete(%s): ignoring (%s)%s' % (key, ptr.container, ptr))
                    all_ptrs.append(ptr)
                    continue
                try:
                    self.__delitem__(ptr.ptr_path, *gi_args)
                    deleted_c += 1
                except PleaseUnlockError:
                    raise
                except KeyError:
                    missing_c += 1
                except (OSError, IOError):
                    all_ptrs.append(ptr)
                    errored_c += 1

            md[md.OFS_POINTERS] = all_ptrs
            if errored_c:
                failed.append(md)
            elif deleted_c or missing_c:
                # Deleted or already gone, we are happy
                deleted.append(md)
            else:
                ignored.append(md)

        return (deleted, ignored, failed, moved)
