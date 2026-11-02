"""
core/draft_state.py — persistent "draft" (default) values for the wizard's inputs.

WHY THIS EXISTS
---------------
Streamlit deletes a keyed widget's value from st.session_state the moment a
rerun finishes without that widget having been rendered. In this app that
happens constantly: closing a dialog (Cancel / "Use this value" / the X),
switching a step's "I already have this value" <-> "Help me calculate it"
radio, collapsing the Step 3 workflow panel, and so on. The result was that
everything typed into the Gt/Gr/Pt/Pr(p)/propagation dialogs was thrown away
on close, and values pre-filled from the ITU .mdb import survived only until
the first time the dialog was closed.

HOW IT WORKS
------------
Every input is mirrored into st.session_state["drafts"] — a plain dict, which
Streamlit never cleans up. The wrappers below (number / text / checkbox /
radio / selectbox) do two things around the real Streamlit widget:

  before it is created : if the widget's key is missing (first open, or it was
                         cleaned up), seed it from the draft store; the
                         hard-coded `default` is only used when nothing has
                         been saved yet.
  after it is created  : write the current value back into the store.

Two widgets in different dialogs can share ONE draft through `share="name"`
(e.g. the earth-station latitude in the Gt calculator and in Step 5). Editing
it in either place updates the draft, and the other widget re-seeds from it
the next time it renders.

Anything that changes a draft programmatically (MDB import, auto-fill
buttons) must use put(), NOT st.session_state[widget_key] = ..., so every
widget bound to that draft re-seeds from the new value.

Only values that differ from the default (or were explicitly put()) are
stored. That keeps "auto" defaults (e.g. p(%) derived from frequency, the
Pt dialog's gain prefill) able to keep following their source until the user
actually edits the field — see `follow_default`.
"""
import streamlit as st

_MISSING = object()
_STORE_KEY = "drafts"
_REGISTRY_KEY = "_draft_widget_keys"


def _store():
    return st.session_state.setdefault(_STORE_KEY, {})


def _registry():
    """{draft_name: {widget_key, ...}} — every widget key that has ever been
    bound to that draft, so put()/sync can invalidate them."""
    return st.session_state.setdefault(_REGISTRY_KEY, {})


# ---------------------------------------------------------------------------
# Public read / write API
# ---------------------------------------------------------------------------
def get(name, default=None):
    """Read a draft value (None / `default` if nothing has been saved)."""
    return _store().get(name, default)


def put(name, value):
    """
    Programmatically set a draft (e.g. from the ITU .mdb import or an
    "auto-fill" button). Every widget bound to this draft is invalidated so it
    re-seeds from the new value the next time it renders.
    """
    _store()[name] = value
    for key in list(_registry().get(name, ())):
        st.session_state.pop(key, None)
        st.session_state.pop(f"{key}__default", None)


def clear_all():
    """Forget every draft (used by the wizard's "Clear all" button)."""
    for keys in _registry().values():
        for key in keys:
            st.session_state.pop(key, None)
            st.session_state.pop(f"{key}__default", None)
    st.session_state[_STORE_KEY] = {}


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _seed(key, name, default, *, follow_default, coerce, valid=None):
    """Make sure st.session_state[key] holds the right value BEFORE the
    widget is instantiated (assigning afterwards would raise)."""
    ss = st.session_state
    _registry().setdefault(name, set()).add(key)
    store = _store()

    want = default
    if name in store:
        try:
            cand = coerce(store[name])
            if valid is None or valid(cand):
                want = cand
        except (TypeError, ValueError):
            pass

    default_key = f"{key}__default"
    last_default = ss.get(default_key, _MISSING)

    if key not in ss:
        ss[key] = want
    elif (follow_default and name not in store
          and last_default is not _MISSING and last_default != default
          and ss[key] == last_default):
        # The user never touched this field and its computed default has
        # moved (e.g. p(%) follows the frequency) -> follow it.
        ss[key] = default
    ss[default_key] = default


def _sync(key, name, value, default):
    """Write the widget's current value back to the draft store."""
    store = _store()
    if name not in store and value == default:
        return  # untouched default: don't freeze it as a "saved" value
    if store.get(name, _MISSING) == value:
        return
    store[name] = value
    # Widgets in other dialogs bound to the same shared draft must re-seed.
    for other in list(_registry().get(name, ())):
        if other != key:
            st.session_state.pop(other, None)
            st.session_state.pop(f"{other}__default", None)


# ---------------------------------------------------------------------------
# Widget wrappers. `key` is the real Streamlit widget key; `share` (optional)
# is the draft name, defaulting to `key`. Do not pass value=/index= — the
# draft store decides the initial value.
# ---------------------------------------------------------------------------
def number(label, key, default=0.0, *, share=None, follow_default=False, **kwargs):
    name = share or key
    default = float(default)
    _seed(key, name, default, follow_default=follow_default, coerce=float)
    value = st.number_input(label, key=key, **kwargs)
    if value is None:
        value = default
    _sync(key, name, value, default)
    return value


def text(label, key, default="", *, share=None, follow_default=False, **kwargs):
    name = share or key
    default = str(default)
    _seed(key, name, default, follow_default=follow_default, coerce=str)
    value = st.text_input(label, key=key, **kwargs)
    _sync(key, name, value, default)
    return value


def checkbox(label, key, default=False, *, share=None, **kwargs):
    name = share or key
    default = bool(default)
    _seed(key, name, default, follow_default=False, coerce=bool)
    value = st.checkbox(label, key=key, **kwargs)
    _sync(key, name, value, default)
    return value


def radio(label, options, key, default=None, *, share=None, **kwargs):
    options = list(options)
    name = share or key
    default = options[0] if default is None else default
    _seed(key, name, default, follow_default=False, coerce=lambda v: v,
          valid=lambda v: v in options)
    value = st.radio(label, options, key=key, **kwargs)
    _sync(key, name, value, default)
    return value


def selectbox(label, options, key, default=None, *, share=None, **kwargs):
    options = list(options)
    name = share or key
    default = options[0] if default is None else default
    _seed(key, name, default, follow_default=False, coerce=lambda v: v,
          valid=lambda v: v in options)
    value = st.selectbox(label, options, key=key, **kwargs)
    _sync(key, name, value, default)
    return value