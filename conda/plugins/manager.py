# Copyright (C) 2012 Anaconda, Inc
# SPDX-License-Identifier: BSD-3-Clause
from __future__ import annotations

import functools

import pluggy

from . import solvers, virtual_packages
from .hookspec import CondaSpecs, spec_name
from ..auxlib.ish import dals
from ..base.context import context
from ..core.solve import Solver
from ..exceptions import CondaValueError, PluginError


class CondaPluginManager(pluggy.PluginManager):
    """
    The conda plugin manager to implement behavior additional to
    pluggy's default plugin manager.
    """
    #: Cached version of the :meth:`~conda.plugins.manager.CondaPluginManager.get_solver_backend`
    #: method.
    get_cached_solver_backend = None

    def __init__(self, project_name: str | None = None, *args, **kwargs) -> None:
        # Setting the default project name to the spec name for ease of use
        if project_name is None:
            project_name = spec_name
        super().__init__(project_name, *args, **kwargs)
        # Make the cache containers local to the instances so that the
        # reference from cache to the instance gets garbage collected with the instance
        self.get_cached_solver_backend = functools.lru_cache(maxsize=None)(self.get_solver_backend)

    def load_plugins(self, *plugins) -> list[str]:
        """
        Load the provided list of plugins and fail gracefully on error.
        The provided list plugins can either be classes or modules with
        :attr:`~conda.plugins.hook_impl`.
        """
        plugin_names = []
        for plugin in plugins:
            try:
                plugin_name = self.register(plugin)
            except ValueError as err:
                raise PluginError(
                    f"Error while loading conda plugins from {plugins}: {err}"
                )
            else:
                plugin_names.append(plugin_name)
        return plugin_names

    def load_setuptools_entrypoints(self, *args, **kwargs) -> int:
        """
        Overloading the parent method from pluggy to add conda specific exceptions.

        See :meth:`pluggy.PluginManager.load_setuptools_entrypoints` for
        more information.
        """
        try:
            return super().load_setuptools_entrypoints(*args, **kwargs)
        except Exception as err:
            raise PluginError(
                f"Error while loading conda plugins from entrypoints: {err}"
            )

    def get_hook_results(self, name: str) -> list:
        """
        Return results of the plugin hooks with the given name and
        raise an error if there is an conflict.
        """
        specname = f"{self.project_name}_{name}"  # e.g. conda_solvers
        hook = getattr(self.hook, specname, None)
        if hook is None:
            raise PluginError(f"Could not load `{specname}` plugins.")

        plugins = sorted(
            (item for items in hook() for item in items),
            key=lambda item: item.name,
        )
        # Check for conflicts
        seen = set()
        conflicts = [plugin for plugin in plugins if plugin.name in seen or seen.add(plugin.name)]
        if conflicts:
            raise PluginError(
                dals(
                    f"""
                    Conflicting `{name}` plugins found:

                    {', '.join([str(conflict) for conflict in conflicts])}

                    Multiple conda plugins are registered via the `{specname}` hook.
                    Please make sure that you don't have any incompatible plugins installed.
                    """
                )
            )
        return plugins

    def get_solver_backend(self, name: str = None) -> type[Solver]:
        """
        Get the solver backend with the given name (or fall back to the
        name provided in the context).

        See ``context.solver`` for more details.

        Please use the cached version of this method called
        :meth:`get_cached_solver_backend` for high-throughput code paths
        which is set up as a instance-specific LRU cache.
        """
        # Some light data validation in case name isn't given.
        if name is None:
            name = context.solver
        name = name.lower()

        # Build a mapping between a lower cased backend name and
        # solver backend class provided by the installed plugins.
        solvers_mapping = {
            solver.name.lower(): solver.backend
            for solver in self.get_hook_results("solvers")
        }

        # Look up the solver mapping an fail loudly if it can't
        # find the requested solver.
        backend = solvers_mapping.get(name, None)
        if backend is None:
            raise CondaValueError(
                f"You have chosen a non-default solver backend ({name}) "
                f"but it was not recognized. Choose one of: "
                f"{', '.join(solvers_mapping.keys())}"
            )

        return backend


@functools.lru_cache(maxsize=None)  # FUTURE: Python 3.9+, replace w/ functools.cache
def get_plugin_manager() -> CondaPluginManager:
    """
    Get a cached version of the :class:`~conda.plugins.manager.CondaPluginManager`
    instance, with the built-in and the entrypoints provided plugins loaded.
    """
    plugin_manager = CondaPluginManager()
    plugin_manager.add_hookspecs(CondaSpecs)
    plugin_manager.load_plugins(solvers, *virtual_packages.plugins)
    plugin_manager.load_setuptools_entrypoints(spec_name)
    return plugin_manager