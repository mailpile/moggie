import logging

from ...util.friendly import friendly_datetime
from .multichoicedialog import MultiChoiceDialog


class UndoDialog(MultiChoiceDialog):
    DEFAULT_OK = 'Undo'

    def __init__(self, tui):
        self.undo_map = {}
        for pair in reversed(tui.undoable):
             inf = pair[1]
             key = '%s - %s' % (friendly_datetime(inf['ts']), inf['comment'])
             retry = 1
             while key in self.undo_map:
                 retry += 1
                 key = '%s - %s (#%d)' % (
                     friendly_datetime(inf['ts']), inf['comment'], retry)
             self.undo_map[key] = pair
             if len(self.undo_map) >= 12:
                 break

        logging.debug('Undoable: %s' % (self.undo_map,))
        super().__init__(tui, list(self.undo_map.keys()),
            title='Undo recent operations',
            action=self.do_undo)

    def normalize(self, value):
        return value

    def do_undo(self, choice, pressed=None):
        # Usually the method will be a mog_ctx.tag, but we allow for
        # other undoable events as well here. As long as they use the
        # same interface and include ts, comment and id in the info.
        method, info = pair = self.undo_map.get(choice)
        method(
            undo=info['id'],
            on_success=lambda *a: self.undid(pair))

    def undid(self, pair):
        self.tui.undoable.remove(pair)
        self.tui.refresh_all()
