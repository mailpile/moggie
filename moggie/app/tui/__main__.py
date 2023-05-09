from . import TuiConnManager

collected = []
def on_message(source, data):
    collected.append('%s said %s' % (source, data))

tr = TuiConnManager(None, None, None)
hid = tr.add_handler('elephant:*', 'Test handler', on_message)

tr.handle_message('elephant', '{"1": "a JSON string"}')
tr.handle_message('elephant', '{"1": "a JSON string"}')
assert(len(collected) == 2)

tr.handle_message('partridge', '{"1": "a JSON string"}')
assert(len(collected) == 2)

tr.del_handler(hid)
tr.del_handler(hid)  # Duplicates are OK
tr.handle_message('elephant', '{"1": "a JSON string"}')
assert(len(collected) == 2)

print('Tests passed OK')
