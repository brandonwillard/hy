# Copyright 2018 the authors.
# This file is part of Hy, which is free software licensed under the Expat
# license. See the LICENSE.
import importlib
import inspect
import pkgutil

from hy._compat import PY3, string_types
from hy.models import replace_hy_obj, HyExpression, HySymbol, wrap_value
from hy.lex import mangle

from hy.errors import HyTypeError, HyMacroExpansionError

try:
    # Check if we have the newer inspect.signature available.
    # Otherwise fallback to the legacy getargspec.
    inspect.signature  # noqa
except AttributeError:
    def has_kwargs(fn):
        argspec = inspect.getargspec(fn)
        return argspec.keywords is not None

    def format_args(fn):
        argspec = inspect.getargspec(fn)
        return inspect.formatargspec(*argspec)

else:
    def has_kwargs(fn):
        parameters = inspect.signature(fn).parameters
        return any(param.kind == param.VAR_KEYWORD
                   for param in parameters.values())

    def format_args(fn):
        return str(inspect.signature(fn))


CORE_MACROS = [
    "hy.core.bootstrap",
]

EXTRA_MACROS = [
    "hy.core.macros",
]


def macro(name):
    """Decorator to define a macro called `name`.
    """
    name = mangle(name)
    def _(fn):
        fn.__name__ = '({})'.format(name)
        try:
            fn._hy_macro_pass_compiler = has_kwargs(fn)
        except Exception:
            # An exception might be raised if fn has arguments with
            # names that are invalid in Python.
            fn._hy_macro_pass_compiler = False

        module = inspect.getmodule(fn)
        module_macros = module.__dict__.setdefault('__macros__', {})
        module_macros[name] = fn

        return fn
    return _


def tag(name):
    """Decorator to define a tag macro called `name`.
    """
    def _(fn):
        _name = mangle('#{}'.format(name))

        if not PY3:
            _name = _name.encode('UTF-8')

        fn.__name__ = _name

        module = inspect.getmodule(fn)

        module_name = module.__name__
        if module_name.startswith("hy.core"):
            module_name = None

        module_tags = module.__dict__.setdefault('__tags__', {})
        module_tags[mangle(name)] = fn

        return fn
    return _


def _same_modules(source_module, target_module):
    """Compare the filenames associated with the given modules names.

    This tries to not actually load the modules.
    """
    if not (source_module or target_module):
        return False

    if target_module == source_module:
        return True

    def _get_filename(module):
        filename = None
        try:
            if not inspect.ismodule(module):
                loader = pkgutil.get_loader(module)
                if loader:
                    filename = loader.get_filename()
            else:
                filename = inspect.getfile(module)
        except (TypeError, ImportError):
            pass

        return filename

    source_filename = _get_filename(source_module)
    target_filename = _get_filename(target_module)

    return (source_filename and target_filename and
            source_filename == target_filename)


def require(source_module, target_module, assignments, prefix=""):
    """Load macros from one module into the namespace of another.

    This function is called from the `require` special form in the compiler.

    Parameters
    ----------
    source_module: str or types.ModuleType
        The module from which macros are to be imported.

    target_module: str, types.ModuleType or None
        The module into which the macros will be loaded.  If `None`, then
        the caller's namespace.
        The latter is useful during evaluation of generated AST/bytecode.

    assignments: str or list of tuples of strs
        The string "ALL" or a list of macro name and alias pairs.

    prefix: str, optional ("")
        If nonempty, its value is prepended to the name of each imported macro.
        This allows one to emulate namespaced macros, like
        "mymacromodule.mymacro", which looks like an attribute of a module.

    Returns
    -------
    out: boolean
        Whether or not macros and tags were actually transferred.
    """

    if target_module is None:
        parent_frame = inspect.stack()[1][0]
        target_namespace = parent_frame.f_globals
        target_module = target_namespace.get('__name__', None)
    elif isinstance(target_module, string_types):
        target_module = importlib.import_module(target_module)
        target_namespace = target_module.__dict__
    elif inspect.ismodule(target_module):
        target_namespace = target_module.__dict__
    else:
        raise TypeError('`target_module` is not a recognized type: {}'.format(
            type(target_module)))

    # Let's do a quick check to make sure the source module isn't actually
    # the module being compiled (e.g. when `runpy` executes a module's code
    # in `__main__`).
    # We use the module's underlying filename for this (when they exist), since
    # it's the most "fixed" attribute.
    if _same_modules(source_module, target_module):
        return False

    if not inspect.ismodule(source_module):
        source_module = importlib.import_module(source_module)

    source_macros = source_module.__dict__.setdefault('__macros__', {})
    source_tags = source_module.__dict__.setdefault('__tags__', {})

    if len(source_module.__macros__) + len(source_module.__tags__) == 0:
        if assignments != "ALL":
            raise ImportError('The module {} has no macros or tags'.format(
                source_module))
        else:
            return False

    target_macros = target_namespace.setdefault('__macros__', {})
    target_tags = target_namespace.setdefault('__tags__', {})

    if prefix:
        prefix += "."

    if assignments == "ALL":
        # Only add macros/tags created in/by the source module.
        name_assigns = [(n, n) for n, f in source_macros.items()
                        if inspect.getmodule(f) == source_module]
        name_assigns += [(n, n) for n, f in source_tags.items()
                         if inspect.getmodule(f) == source_module]
    else:
        # If one specifically requests a macro/tag not created in the source
        # module, I guess we allow it?
        name_assigns = assignments

    for name, alias in name_assigns:
        _name = mangle(name)
        alias = mangle(prefix + alias)
        if _name in source_module.__macros__:
            target_macros[alias] = source_macros[_name]
        elif _name in source_module.__tags__:
            target_tags[alias] = source_tags[_name]
        else:
            raise ImportError('Could not require name {} from {}'.format(
                _name, source_module))

    return True


def load_macros(module):
    """Load the hy builtin macros for module `module_name`.

    Modules from `hy.core` can only use the macros from CORE_MACROS.
    Other modules get the macros from CORE_MACROS and EXTRA_MACROS.
    """
    builtin_macros = CORE_MACROS

    if not module.__name__.startswith("hy.core"):
        builtin_macros += EXTRA_MACROS

    module_macros = module.__dict__.setdefault('__macros__', {})
    module_tags = module.__dict__.setdefault('__tags__', {})

    for builtin_mod_name in builtin_macros:
        builtin_mod = importlib.import_module(builtin_mod_name)

        # Make sure we don't overwrite macros in the module.
        if hasattr(builtin_mod, '__macros__'):
            module_macros.update({k: v
                                  for k, v in builtin_mod.__macros__.items()
                                  if k not in module_macros})
        if hasattr(builtin_mod, '__tags__'):
            module_tags.update({k: v
                                for k, v in builtin_mod.__tags__.items()
                                if k not in module_tags})


def make_empty_fn_copy(fn):
    try:
        # This might fail if fn has parameters with funny names, like o!n. In
        # such a case, we return a generic function that ensures the program
        # can continue running. Unfortunately, the error message that might get
        # raised later on while expanding a macro might not make sense at all.

        formatted_args = format_args(fn)
        fn_str = 'lambda {}: None'.format(
            formatted_args.lstrip('(').rstrip(')'))
        empty_fn = eval(fn_str)

    except Exception:

        def empty_fn(*args, **kwargs):
            None

    return empty_fn


def macroexpand(tree, module, compiler=None, once=False):
    """Expand the toplevel macros for the given Hy AST tree.

    Load the macros from the given `module`, then expand the (top-level) macros
    in `tree` until we no longer can.

    `HyExpression` resulting from macro expansions are assigned the module in
    which the macro function is defined (determined using `inspect.getmodule`).
    If the resulting `HyExpression` is itself macro expanded, then the
    namespace of the assigned module is checked first for a macro corresponding
    to the expression's head/car symbol.  If the head/car symbol of such a
    `HyExpression` is not found among the macros of its assigned module's
    namespace, the outer-most namespace--e.g.  the one given by the `module`
    parameter--is used as a fallback.

    Parameters
    ----------
    tree: HyObject or list
        Hy AST tree.

    module: str or types.ModuleType
        Module used to determine the local namespace for macros.

    compiler: HyASTCompiler, optional
        The compiler object passed to expanded macros.

    once: boolean, optional
        Only expand the first macro in `tree`.

    Returns
    ------
    out: HyObject
        Returns a mutated tree with macros expanded.
    """
    if not inspect.ismodule(module):
        module = importlib.import_module(module)

    assert not compiler or compiler.module == module

    while True:

        if not isinstance(tree, HyExpression) or tree == []:
            break

        fn = tree[0]
        if fn in ("quote", "quasiquote") or not isinstance(fn, HySymbol):
            break

        fn = mangle(fn)
        expr_modules = (([] if not hasattr(tree, 'module') else [tree.module])
            + [module])

        # Choose the first namespace with the macro.
        m = next((mod.__macros__[fn]
                  for mod in expr_modules
                  if fn in mod.__macros__),
                 None)
        if not m:
            break

        opts = {}
        if m._hy_macro_pass_compiler:
            if compiler is None:
                from hy.compiler import HyASTCompiler
                compiler = HyASTCompiler(module)
            opts['compiler'] = compiler

        try:
            m_copy = make_empty_fn_copy(m)
            m_copy(module.__name__, *tree[1:], **opts)
        except TypeError as e:
            msg = "expanding `" + str(tree[0]) + "': "
            msg += str(e).replace("<lambda>()", "", 1).strip()
            raise HyMacroExpansionError(tree, msg)

        try:
            obj = m(module.__name__, *tree[1:], **opts)
        except HyTypeError as e:
            if e.expression is None:
                e.expression = tree
            raise
        except Exception as e:
            msg = "expanding `" + str(tree[0]) + "': " + repr(e)
            raise HyMacroExpansionError(tree, msg)

        if isinstance(obj, HyExpression):
            obj.module = inspect.getmodule(m)

        tree = replace_hy_obj(obj, tree)

        if once:
            break

    tree = wrap_value(tree)
    return tree


def macroexpand_1(tree, module, compiler=None):
    """Expand the toplevel macro from `tree` once, in the context of
    `compiler`."""
    return macroexpand(tree, module, compiler, once=True)


def tag_macroexpand(tag, tree, module):
    """Expand the tag macro `tag` with argument `tree`."""
    if not inspect.ismodule(module):
        module = importlib.import_module(module)

    expr_modules = (([] if not hasattr(tree, 'module') else [tree.module])
        + [module])

    # Choose the first namespace with the macro.
    tag_macro = next((mod.__tags__[tag]
                      for mod in expr_modules
                      if tag in mod.__tags__),
                     None)

    if tag_macro is None:
        raise HyTypeError(tag, "'{0}' is not a defined tag macro.".format(tag))

    expr = tag_macro(tree)

    if isinstance(expr, HyExpression):
        expr.module = inspect.getmodule(tag_macro)

    return replace_hy_obj(expr, tree)
