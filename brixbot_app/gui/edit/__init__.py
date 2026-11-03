"""The project editor: form widgets (fields.py), one editor per item kind
(items.py) and the dock panel that holds them (panel.py).

Every editor is built from a copy of an item's JSON and returns the edited
JSON from value(). Keys an editor does not manage (comments, future keys)
are kept, so a form round trip never loses data.
"""
