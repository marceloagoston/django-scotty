import importlib
import inspect
import logging
import os
import pkgutil
import re
import uuid

from typing import List
from urllib.parse import parse_qs, urlencode

from crispy_forms.helper import FormHelper
from django.conf import settings
from django_scotty.constants import (
    BUTTONS_VARIANTS,
    BTN_DANGER,
    BTN_DARK,
    BTN_INFO,
    BTN_LIGHT,
    BTN_OUTLINE_DANGER,
    BTN_OUTLINE_DARK,
    BTN_OUTLINE_INFO,
    BTN_OUTLINE_LIGHT,
    BTN_OUTLINE_PRIMARY,
    BTN_OUTLINE_SECONDARY,
    BTN_OUTLINE_SUCCESS,
    BTN_OUTLINE_WARNING,
    BTN_PRIMARY,
    BTN_SECONDARY,
    BTN_SUCCESS,
    BTN_WARNING,
    ENTRY_BTN_CLASS,
    ENTRY_STYLE,
    ENTRY_VARIANT,
    STYLE_OUTLINE,
    STYLE_SOLID,
    TABLE_EMPTY_TEXT,
    VARIANT_DANGER,
    VARIANT_DARK,
    VARIANT_INFO,
    VARIANT_LIGHT,
    VARIANT_PRIMARY,
    VARIANT_SECONDARY,
    VARIANT_SUCCESS,
    VARIANT_WARNING,
)
from django_scotty.form_helpers import get_form_buttons
from django.core.paginator import EmptyPage, Paginator
from django.db.models import QuerySet
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.urls import path, reverse, NoReverseMatch
from django.utils.safestring import SafeText
from django.views.generic import CreateView, DeleteView, DetailView, UpdateView
from django_filters.views import FilterView
from django_tables2.export.views import ExportMixin
from django_tables2.views import SingleTableMixin, SingleTableView
import django_tables2 as tables

logger = logging.getLogger(__name__)

DEFAULT_EMPTY_MSG = "- No hay datos para mostrar -"
def get_scotty_setting(key, default=None):
    """Read a value from ``settings.SCOTTY_CONFIG`` with fallback.

    Central access point for all scotty configuration so views don't
    reach into ``django.conf.settings`` directly.
    """
    config = getattr(settings, "SCOTTY_CONFIG", {})
    return config.get(key, default)


# Button variant classes.
# Useful when using a non-Bootstrap library — override the CSS classes
# that control button styles.
# Each variant can define ``outline`` and ``solid`` styles.
# Projects can override via ``SCOTTY_CONFIG``::
# 
#     SCOTTY_CONFIG = {
#         "buttons_variants": {
#             "primary":   {"outline": "btn-outline-primary",   "solid": "btn-primary"},
#             "secondary": {"outline": "btn-outline-secondary", "solid": "btn-secondary"},
#             "danger":    {"outline": "btn-outline-danger",    "solid": "btn-danger"},
#         },
#         ...
#     }
#
# For backward compatibility, a plain string is also accepted:
#     "primary": "btn-primary"

BUTTON_VARIANT_DEFAULTS = {
    VARIANT_PRIMARY:   {STYLE_OUTLINE: BTN_OUTLINE_PRIMARY,   STYLE_SOLID: BTN_PRIMARY},
    VARIANT_SECONDARY: {STYLE_OUTLINE: BTN_OUTLINE_SECONDARY, STYLE_SOLID: BTN_SECONDARY},
    VARIANT_SUCCESS:   {STYLE_OUTLINE: BTN_OUTLINE_SUCCESS,   STYLE_SOLID: BTN_SUCCESS},
    VARIANT_DANGER:    {STYLE_OUTLINE: BTN_OUTLINE_DANGER,    STYLE_SOLID: BTN_DANGER},
    VARIANT_WARNING:   {STYLE_OUTLINE: BTN_OUTLINE_WARNING,   STYLE_SOLID: BTN_WARNING},
    VARIANT_INFO:      {STYLE_OUTLINE: BTN_OUTLINE_INFO,      STYLE_SOLID: BTN_INFO},
    VARIANT_LIGHT:     {STYLE_OUTLINE: BTN_OUTLINE_LIGHT,     STYLE_SOLID: BTN_LIGHT},
    VARIANT_DARK:      {STYLE_OUTLINE: BTN_OUTLINE_DARK,      STYLE_SOLID: BTN_DARK},
}


def get_button_class(variant=VARIANT_PRIMARY, style=STYLE_SOLID):
    """Resolve a button variant + style to a CSS class.

    Looks up ``variant`` in ``SCOTTY_CONFIG["buttons_variants"]``.
    Falls back to ``BUTTON_VARIANT_DEFAULTS``, then to ``"btn-primary"``.

    Parameters
    ----------
    variant : str
        One of ``"primary"``, ``"secondary"``, ``"danger"`` (default ``"primary"``).
    style : str
        ``"solid"`` (default) or ``"outline"``. Ignored when the variant
        value is a plain string (backward-compat).

    Returns
    -------
    str
        Full Bootstrap button class, e.g. ``"btn-primary"`` or ``"btn-outline-primary"``.
    """
    variants = get_scotty_setting(BUTTONS_VARIANTS, BUTTON_VARIANT_DEFAULTS)
    entry = variants.get(variant, BUTTON_VARIANT_DEFAULTS.get(variant, BTN_PRIMARY))

    if isinstance(entry, str):
        return entry  # backward-compat: flat string
    return entry.get(style, BTN_PRIMARY)


class ActionTable(tables.Table):
    def __init__(self, *args, **kwargs):
        self.action_columns = kwargs.pop("available_actions", [])
        self.post_paginate_hook = kwargs.pop("post_paginate_hook", None)
        super().__init__(*args, **kwargs)

    acciones = tables.Column(verbose_name="Acciones", orderable=False, empty_values=())

    def get_ver_link(self, url):
        """Render a link to a URL as a "view" button."""
        return SafeText(f'<a href="{url}" class="btn boton-ver"></a>')

    # TODO: Test
    def render_acciones(self, record):
        """Render all available actions.
        Single action renders as a button; multiple actions render
        as a grouped dropdown."""

        rendered_edit = SafeText("")
        if getattr(self, "updateview_class", None) is not None:
            try:
                edit_url = reverse(self.update_url_name, kwargs={"pk": record.pk})
                if getattr(self, "usar_modal", False):
                    modal_id = f"modal-{self.unique_id}"
                    rendered_edit = SafeText(
                        f'<button class="btn {BTN_WARNING} btn-sm"'
                        f' hx-get="{edit_url}?_mid={self.unique_id}"'
                        f' hx-target="#{modal_id}-body"'
                        f' hx-swap="innerHTML"'
                        f' data-bs-toggle="modal"'
                        f' data-bs-target="#{modal_id}">Editar</button>'
                    )
                else:
                    rendered_edit = SafeText(
                        f'<button class="btn {BTN_WARNING} btn-sm"'
                        f' hx-get="{edit_url}"'
                        f' hx-target="#main-content"'
                        f' hx-swap="innerHTML"'
                        f' hx-push-url="true">Editar</button>'
                    )
            except Exception as err:
                logging.error(f"[SCOTTY LOADER] Error rendering edit button {err}")

        rendered_delete = SafeText("")
        if getattr(self, "deleteview_class", None) is not None:
            try:
                delete_url = reverse(self.delete_url_name, kwargs={"pk": record.pk})
                delete_mid = f"delete-{self.unique_id}"
                modal_id = f"modal-{delete_mid}"
                rendered_delete = SafeText(
                    f'<button class="btn {BTN_DANGER} btn-sm ms-1"'
                    f' hx-get="{delete_url}?_mid={delete_mid}"'
                    f' hx-target="#{modal_id}-body"'
                    f' hx-swap="innerHTML"'
                    f' data-bs-toggle="modal"'
                    f' data-bs-target="#{modal_id}">Eliminar</button>'
                )
            except Exception:
                pass

        if getattr(self, "url_action_method", None) is None:
            return rendered_edit + rendered_delete

        rendered_actions = SafeText("")
        url = reverse(self.url_action_method)
        if len(self.action_columns) == 1:
            accion = self.action_columns[0]
            accion_method = getattr(self.view, accion[0])

            # TODO: Test
            try:
                condition_result = accion_method.condition(record, self.request)
                if not condition_result:
                    return rendered_edit + rendered_delete
            except Exception:
                return rendered_edit + rendered_delete

            show_confirm = getattr(accion_method, "show_confirm", False)
            confirm_attr = (
                'hx-confirm="¿Está seguro que desea realizar esta acción?"'
                if show_confirm
                else ""
            )
            button_html = f"""<button hx-post=\"{url}?pk={record.pk}&action={accion[0]}\"
                    hx-trigger=\"click\"
                    hx-swap=\"outerHTML\"
                    class=\"btn {BTN_PRIMARY}\"
                    hx-indicator=\"#spinner-load\"
                    type=\"btn\"
                    {confirm_attr}>{accion[1]}</button>"""
            return rendered_edit + rendered_delete + SafeText(button_html)
        elif len(self.action_columns) > 1:
            rendered_actions = SafeText("")
            for accion in self.action_columns:
                accion_method = getattr(self.view, accion[0])

                try:
                    condition_result = accion_method.condition(record, self.request)
                    if not condition_result:
                        continue
                except Exception:
                    pass

                show_confirm = getattr(accion_method, "show_confirm", False)
                confirm_attr = (
                    'hx-confirm="¿Está seguro que desea realizar esta acción?"'
                    if show_confirm
                    else ""
                )
                action_html = f"""<li>
                    <a hx-post=\"{url}?pk={record.pk}&action={accion[0]}\"
                    hx-trigger=\"click\"
                    hx-swap=\"outerHTML\"
                    hx-indicator=\"#spinner-load\"
                    class=\"dropdown-item\"
                    {confirm_attr}>{accion[1]}</a>
                    </li>"""
                rendered_actions += SafeText(action_html)

            return (
                rendered_edit
                + rendered_delete
                + SafeText(f"""
                            <div class="btn-group">
                            <button type="button"
                            class="btn {BTN_PRIMARY} dropdown-toggle"
                            data-bs-toggle="dropdown" aria-expanded="false">
                                Acciones
                            </button>
                            <ul class="dropdown-menu">
                                {rendered_actions}
                            </ul>
                            </div>""")
            )
        else:
            return rendered_edit + rendered_delete

    def paginate(self, *args, **kwargs):
        # Call the original method first
        super().paginate(*args, **kwargs)

        # Now that pagination has run, 'self.page' exists
        if self.page and self.post_paginate_hook:
            self.post_paginate_hook(self.page.object_list)


# TODO: Test
class PaginationFixMixin:
    """Mixin to handle pagination errors when filters are applied."""

    def get(self, request, *args, **kwargs):
        """Override get method to handle pagination issues"""

        try:
            return super().get(request, *args, **kwargs)
        except (EmptyPage, Http404):
            try:
                queryset = self.get_queryset()

                if hasattr(self, "get_filterset") and hasattr(self, "filterset_class"):
                    filterset = self.get_filterset(self.filterset_class)
                    if filterset.is_valid():
                        queryset = filterset.qs

                paginator = Paginator(queryset, self.paginate_by)
                total_pages = paginator.num_pages

                if total_pages > 0:
                    target_page = total_pages
                else:
                    target_page = 1

            except Exception:
                target_page = 1

            get_params = request.GET.copy()
            get_params["page"] = str(target_page)

            redirect_url = f"{request.path}?{get_params.urlencode()}"
            return redirect(redirect_url)


class CottonTableView(PaginationFixMixin, ExportMixin, SingleTableMixin, FilterView):
    """Base View for django tables with bootstrap and filters."""

    template_name = "django_tables2/base_django_tables2.html"
    formhelper_class = FormHelper
    paginate_by = 10
    available_action_names = None
    show_boton_nuevo = False
    usar_modal = False
    create_url = None
    createview_class = None
    updateview_class = None
    deleteview_class = None
    post_paginate_hook = None
    pre_render_hook = None
    title = "Listado"
    subtitle = None
    table_empty_text = None
    # Control whether to show the "Action on selected" UI
    show_bulk_actions = True
    # Unified filter-button system
    available_filter_buttons = [
        "filtrar",
        "exportar_xls",
    ]

    # Extra header buttons rendered on the right side of the page title.
    #
    # ``extra_links_actions`` is a **list of method names**. Each name maps to a
    # method on the view. The method provides metadata via **function attributes**
    # (just like Django admin actions).
    #
    # --- Required attributes ---
    #   verbose_name        (str) – visible label for the button.
    #   url                 (str) – Django URL name. Always resolved via
    #                               ``reverse()`` — do NOT use hardcoded paths.
    #
    # --- Optional attributes ---
    #   variant             (str) – "primary" (default), "secondary", "danger",
    #                               "success", "warning", "info", "light", "dark".
    #   style               (str) – "solid" (default) or "outline".
    #   order               (int) – higher values render further right.
    #   allowed_permission  (str) – permission required to show the button.
    #                               Checked via ``user.has_perm()``.
    #                               Override ``_check_user_perms()`` for custom
    #                               logic.
    #   pk                  (str) – keyword argument name from the URL to pass as
    #                               path parameter. ``True`` is a shorthand for
    #                               ``"pk"``. E.g. pk="contrato_id" extracts
    #                               ``self.kwargs.get("contrato_id")``.
    #   params              (dict) – static query parameters merged into the URL.
    #
    # --- Method return ---
    # The method itself may return a ``dict`` with URL parameters. If it returns
    # a non-empty dict, it overrides ``pk`` and ``params`` attributes (with a
    # warning logged if both exist). This allows dynamic params that can't be
    # expressed statically.
    #
    # Methods that are not defined, or lack ``verbose_name`` or ``url``,
    # are silently skipped.
    #
    # --- EXAMPLES ---
    #
    # EXAMPLE 1 — public link with no permission (metadata only)
    #
    #     urls.py → path("help/", …, name="help")
    #
    #     class OrderListView(CottonTableView):
    #         extra_links_actions = ["help"]
    #         def help(self, request):
    #             return {}
    #
    #         help.verbose_name = "Help"
    #         help.url = "help"
    #
    #     # → <a href="/help/" class="btn btn-primary">Help</a>
    #
    # EXAMPLE 2 — link with permission
    #
    #     class OrderListView(CottonTableView):
    #         extra_links_actions = ["config"]
    #
    #         def config(self, request):
    #             return {}
    #
    #         config.verbose_name = "Settings"
    #         config.url = "config"
    #         config.allowed_permission = "orders.config"
    #
    #     # → only users with "orders.config" permission see the button.
    #     #   ``_check_user_perms()`` also lets ``is_staff`` through.
    #
    # EXAMPLE 3 — pk shortcut (extract pk from the current URL)
    #
    #     urls.py → path("contract/<int:pk>/", …, name="contract-detail")
    #
    #     class OrderListView(CottonTableView):
    #         extra_links_actions = ["contract"]
    #
    #         def contract(self, request):
    #             return {}
    #
    #         contract.verbose_name = "View contract"
    #         contract.url = "contract-detail"
    #         contract.pk = True        # self.kwargs.get("pk")
    #         contract.variant = "secondary"
    #
    #     # → <a href="/contract/5/" class="btn btn-secondary">View contract</a>
    #
    # EXAMPLE 4 — pk with custom name
    #
    #     urls.py → path("document/<slug:code>/", …, name="doc-detail")
    #
    #         extra_links_actions = ["document"]
    #
    #         def document(self, request):
    #             return {}
    #
    #         document.verbose_name = "View document"
    #         document.url = "doc-detail"
    #         document.pk = "code"      # self.kwargs.get("code")
    #
    #     # → <a href="/document/ABC-123/" class="btn btn-primary">View document</a>
    #
    # EXAMPLE 5 — static params + order
    #
    #     urls.py → path("export/", …, name="export-list")
    #
    #         extra_links_actions = ["export"]
    #
    #         export.verbose_name = "Export"
    #         export.url = "export-list"
    #         export.params = {"format": "xlsx"}
    #         export.order = 10
    #
    #     # → <a href="/export/?format=xlsx" class="btn btn-primary">Export</a>
    #
    # EXAMPLE 6 — dynamic logic via method return
    #
    #         extra_links_actions = ["recalculate"]
    #
    #         def recalculate(self, request):
    #             mode = "full" if self.object.amount > 10000 else "simple"
    #             return {"pk": self.kwargs.get("pk"), "mode": mode}
    #
    #         recalculate.verbose_name = "Recalculate"
    #         recalculate.url = "recalculate-url"
    #         recalculate.allowed_permission = "orders.recalculate"
    #         recalculate.style = "outline"
    #
    #     # → <a href="/recalculate/5/?mode=full"
    #     #      class="btn btn-outline-primary">Recalculate</a>
    #
    # EXAMPLE 7 — multiple buttons on the same view
    #
    #         extra_links_actions = ["export", "contract", "config"]
    #
    # Note: if ``reverse()`` fails (NoReverseMatch) the original ``url``
    # value is preserved.
    extra_links_actions = []

    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        # TODO: Test view only
        view_only = (
            True if self.request.GET.get("view_only", False) == "true" else False
        )

        if view_only:
            kwargs["available_actions"] = []
        else:
            available_actions = list(self.available_actions)
            kwargs["available_actions"] = available_actions

        kwargs["post_paginate_hook"] = self.post_paginate_hook

        return kwargs

    def get_table(self, **kwargs):
        # Override get_table to pass the view instance to the table
        table = super().get_table(**kwargs)
        table.view = self  # Pass the view instance to the table

        # If a pre_render hook is defined, call it
        if self.pre_render_hook:
            self.pre_render_hook(table)
        return table

    def get_filterset(self, filterset_class):
        kwargs = self.get_filterset_kwargs(filterset_class)
        true_filters = {}
        if kwargs["data"]:
            for key, value in kwargs["data"].items():
                true_filters[key] = value
            kwargs["data"] = true_filters
        filterset = filterset_class(**kwargs)
        filterset.form.helper = self.formhelper_class()
        return filterset

    def _check_user_perms(self, method):
        """Check whether the user has the permission required by *method*.

        ``method.allowed_permission`` is a single permission string.
        If not set, returns ``True`` (no restriction).

        Override this in subclasses to customize permission checking logic.

        Returns:
            bool: ``True`` if the user is allowed to see this button.
        """
        perm_required = getattr(method, "allowed_permission", None)
        if perm_required is None:
            return True
        user = self.request.user

        if user.is_staff:
            return True

        return user.has_perm(perm_required)

    def _get_link_method(self, name):
        """Return the method for *name*, or None if not defined."""
        return getattr(self, name, None)

    def _resolve_link_params(self, name, method):
        """Resolve URL parameters for an extra link.

        1. Base: {} (empty dict)
        2. If method has ``pk`` attribute → merge with self.kwargs
        3. If method has ``params`` attribute → merge
        4. If the method returns a non-empty dict → merge (overrides steps 2-3)
           and log a warning if steps 2-3 had data (conflict).
        """
        params = {}

        # Step 2: pk attribute
        pk_attr = getattr(method, "pk", None)
        if pk_attr is not None:
            pk_key = pk_attr if isinstance(pk_attr, str) else "pk"
            params[pk_key] = self.kwargs.get(pk_key)

        # Step 3: params attribute
        params_attr = getattr(method, "params", None)
        if params_attr:
            params.update(params_attr)

        # Step 4: Call the method (if callable — it should be, but defensively)
        had_attr_params = bool(pk_attr is not None or params_attr)
        try:
            return_dict = method(self.request) or {}
        except Exception:
            return_dict = {}

        if return_dict and had_attr_params:
            logger.warning(
                "%s: method return overrides pk/params attributes", name,
            )

        if return_dict:
            params.update(return_dict)

        return params

    def _get_processed_links(self):
        """Process ``extra_links_actions`` entries into render-ready links.

        Each entry is a method name. The method provides:

        Required attributes:
            - ``verbose_name``: visible label for the button
            - ``url``: Django URL name, always resolved via :func:`reverse`

        Optional attributes:
            - ``variant``, ``style``: Bootstrap button colour / fill
            - ``order``: higher values render further right
            - ``allowed_permission``: single permission string
            - ``pk``: extract a kwarg from URL as path parameter
              (``True`` → ``"pk"``, or a string for a custom kwarg name)
            - ``params``: static dict of query parameters

        The method itself may return a dict of URL parameters. If it returns
        a non-empty dict, it overrides ``pk`` and ``params`` attributes
        (with a warning logged if both exist).

        Methods that are not defined, or lack ``verbose_name`` or ``url``,
        are silently skipped.
        """
        processed_links = []

        for name in self.extra_links_actions:
            method = self._get_link_method(name)
            if method is None:
                continue

            # Required attributes
            verbose_name = getattr(method, "verbose_name", None)
            url = getattr(method, "url", None)
            if not verbose_name or not url:
                continue

            # Permission check (overridable via _check_user_perms)
            if not self._check_user_perms(method):
                continue

            # Resolve URL parameters (pk / query params)
            params = self._resolve_link_params(name, method)

            # Extract pk/id for reverse() kwargs
            pk_value = params.pop("pk", params.pop("id", None))
            reverse_kwargs = {"pk": pk_value} if pk_value is not None else {}

            # Resolve URL via reverse
            try:
                resolved_url = reverse(url, kwargs=reverse_kwargs or None)
            except NoReverseMatch:
                resolved_url = url

            # Build full URL with query string
            query_string = urlencode(params) if params else ""
            full_url = (
                f"{resolved_url}?{query_string}" if query_string else resolved_url
            )

            # Bootstrap button class
            variant = getattr(method, "variant", VARIANT_PRIMARY)
            style = getattr(method, "style", STYLE_SOLID)

            processed_links.append({
                "label": verbose_name,
                "url": full_url,
                "btn_class": get_button_class(variant, style),
                "order": getattr(method, "order", 0),
            })

        processed_links.sort(key=lambda x: x.get("order", 0), reverse=True)
        return processed_links

    def get_context_data(self, **kwargs):
        """Agregamos el total de registros sin filtrar al contexto."""
        context = super().get_context_data(**kwargs)

        orig_table = context["table"]
        orig_table.unfiltered_records = self.model.objects.all().count()
        # TODO: Test view only
        view_only = (
            True if self.request.GET.get("view_only", False) == "true" else False
        )
        if view_only:
            orig_table.available_actions = []
        else:
            orig_table.available_actions = self.available_actions
        trimed_view_name = self.get_slugname()
        orig_table.url_action_method = f"list-view-{trimed_view_name}"
        orig_table.unique_id = get_unique_id("django-table-")
        orig_table.title = self.title
        orig_table.subtitle = self.subtitle
        orig_table.view_only = view_only
        orig_table.show_boton_nuevo = self.show_boton_nuevo
        orig_table.usar_modal = self.usar_modal

        orig_table.empty_text = (
            self.table_empty_text
            if self.table_empty_text is not None
            else get_scotty_setting(TABLE_EMPTY_TEXT, DEFAULT_EMPTY_MSG)
        )
        context["extra_links_actions"] = self._get_processed_links()

        if self.createview_class is not None:
            orig_table.create_url = (
                f"create-view-{self.createview_class.get_slugname()}"
            )
        else:
            orig_table.create_url = (
                self.create_url or f"create-view-{self.get_slugname()}"
            )

        orig_table.updateview_class = self.updateview_class
        orig_table.update_url_name = (
            f"update-view-{self.updateview_class.get_slugname()}"
            if self.updateview_class is not None
            else None
        )

        orig_table.deleteview_class = self.deleteview_class
        orig_table.delete_url_name = (
            f"delete-view-{self.deleteview_class.get_slugname()}"
            if self.deleteview_class is not None
            else None
        )
        context["table"] = orig_table

        # Add control to show/hide bulk actions
        context["show_bulk_actions"] = self.show_bulk_actions

        # Unified filter-button system
        if (
            hasattr(self, "available_filter_buttons")
            and self.available_filter_buttons is not None
        ):
            context["show_action_buttons"] = self.available_filter_buttons
        else:
            # Fallback: build buttons from individual flags
            buttons = []
            if hasattr(self, "show_filter_line") and self.show_filter_line:
                buttons.extend(["filtrar", "limpiar"])
            if hasattr(self, "show_export_xls") and self.show_export_xls:
                buttons.append("exportar_xls")
            context["show_action_buttons"] = buttons

        # Unified logic: when 'filtrar' is included, auto-include 'limpiar'
        action_buttons = context["show_action_buttons"]
        if "filtrar" in action_buttons and "limpiar" not in action_buttons:
            action_buttons = list(action_buttons) + ["limpiar"]
            context["show_action_buttons"] = action_buttons

        return context

    def get_export_filename(self, export_format):
        """Generate a filename based on the view class name."""
        class_name = self.__class__.__name__.replace("View", "")
        filename = re.sub(r"[^\w\s-]", "", class_name.lower())
        filename = re.sub(r"[-\s]+", "_", filename)
        return f"{filename}.{export_format}"

    # TODO: Test
    @property
    def available_actions(self):
        """Yield the short name of each action, if it exists."""
        if self.available_action_names is not None:
            for action in self.available_action_names:
                if hasattr(self, action):
                    action_method = getattr(self, action)
                    verbose_name = getattr(action_method, "verbose_name", None)
                    show_on_bulk = getattr(action_method, "show_on_bulk", True)
                    show_confirm = getattr(action_method, "show_on_bulk", False)
                    if verbose_name is None:
                        verbose_name = action.replace("_", " ").capitalize()

                    yield action, verbose_name, show_on_bulk, show_confirm
        else:
            return []

    # TODO: Test
    # TODO: Allow applying the full selection to the entire filtered queryset
    # via a flag.
    def post(self, request, *args, **kwargs):
        """Handle POST operations on selected items."""

        # The list of checked checkbox IDs, or if a single pk is passed
        if (pk := request.GET.get("pk")) is not None:
            action = request.GET.get("action")
            selected_pks = [pk]
        else:
            action = request.POST.get("action")
            selected_pks = request.POST.getlist("seleccionar")

        queryset_to_act_on: QuerySet = None

        if selected_pks:
            # Case 1: Action on selected items
            queryset_to_act_on = self.model.objects.filter(pk__in=selected_pks)
        elif "filter_query_string" in request.POST:
            # Case 2: Action on the entire filtered queryset
            # Rebuild the filtered queryset without pagination
            filter_params = parse_qs(request.POST["filter_query_string"])
            # Remove pagination params to get the full queryset
            filter_params.pop("page", None)
            filter_params.pop("per_page", None)

            # Use the same filter as in the GET view
            filterset = self.filterset_class(
                filter_params, queryset=self.get_queryset()
            )
            queryset_to_act_on = filterset.qs

        # Execute the action if we have a queryset to process
        if queryset_to_act_on is not None:
            results = []
            for obj in queryset_to_act_on:
                action_method = getattr(self, action)

                # TODO: Test if
                try:
                    condition_result = action_method.condition(obj, self.request)
                    if condition_result:
                        result = getattr(self, action)(obj)
                        results.append(result)
                    else:
                        # Fixme: Add messages
                        # messages.warning(request, 'Could not perform the action')
                        pass
                except Exception:
                    # DO NOT run the action again on error
                    pass

            # FIXME: Improve this logic. Currently if an action returns
            # a redirect, subsequent bulk calls won't execute. Since there
            # is no clear criteria, the first redirect wins.
            if len(results) == 1:
                if hasattr(results[0], "status_code"):
                    return results[0]
            elif len(results) > 1:
                if all(hasattr(result, "status_code") for result in results):
                    return results[0]
        return redirect(request.path)

    # TODO: Test
    @classmethod
    def get_slugname(cls):
        """Return a slugname for the view URL."""
        trimed_view_name = cls.__name__.lower().removesuffix("view")
        return trimed_view_name

    # TASK: consider having a method that returns the url_name directly


def generar_id_valido(base_id):
    """
    Generate a valid HTML/CSS ID from a base string.

    Replaces invalid characters (e.g. '.') with hyphens.
    Ensures the ID starts with a letter by prepending ``id-`` if needed.
    """
    # 1. Replace problematic characters (e.g. dot) with hyphens.
    id_sanitizado = base_id.replace(".", "-")

    # 2. Ensure the ID starts with a letter.
    #    If the first character is a digit, prepend a prefix.
    if id_sanitizado and id_sanitizado[0].isdigit():
        id_valido = f"id-{id_sanitizado}"
    else:
        id_valido = id_sanitizado

    return id_valido


# TODO: Test
def get_unique_id(prefix=""):
    """Generate a unique ID with an optional prefix."""
    component_id = uuid.uuid1().__str__().replace("-", "")[2:8]
    sanitized_id = generar_id_valido(component_id)
    return f"{prefix}{sanitized_id}"


class GenericDetailView(DetailView):
    """
    A generic DetailView that automatically builds a list of object fields
    and values for rendering by a template.
    Override the template for custom layouts.
    """

    # Point to the generic template
    template_name = "django_tables2/generic_detail.html"

    # Optional: fields to never display
    exclude_fields = ["id"]

    def get_context_data(self, **kwargs):
        """
        Override to inject the field list into the context.
        """
        context = super().get_context_data(**kwargs)
        instance = context["object"]

        field_list = []
        # Iterate over all model fields
        for field in instance._meta.get_fields():
            # Many-to-many not yet handled
            if not field.concrete or field.many_to_many:
                continue

            # Skip excluded fields
            if field.name in self.exclude_fields:
                continue

            value = getattr(instance, field.name)

            get_display_method = f"get_{field.name}_display"
            if hasattr(instance, get_display_method):
                value = getattr(instance, get_display_method)()

            if value is None:
                value = "—"

            if isinstance(value, bool):
                value = "Sí" if value else "No"

            try:
                field_list.append(
                    {
                        "label": field.verbose_name.capitalize(),
                        "value": value,
                    }
                )
            except Exception:
                field_list.append(
                    {
                        "label": field.name.capitalize(),
                        "value": value,
                    }
                )

        # Add the field list and title to the context
        context["field_list"] = field_list
        context["title"] = (
            f"Detalle de {instance._meta.verbose_name.capitalize()} {instance.id}"
        )
        return context

    # TODO: Test
    @classmethod
    def get_slugname(cls):
        """Return a slugname for the view URL."""
        trimed_view_name = cls.__name__.lower().removesuffix("detailview")
        return trimed_view_name


class HtmxFormMixin:
    """
    Mixin shared by GenericCreateView and GenericUpdateView.

    Optional attributes:
        partial_template_name (str)  Template fragment loaded via HTMX.
                                     Default: "django_tables2/generic_form_item.html".
        title_form            (str)  Form title. Default: None.
        auto_forms_buttons    (bool) Automatically generates Submit (Save) and
                                     Close/Back buttons based on whether the
                                     form is rendered in a modal or full page.

                                     Appends the buttons at the end of the
                                     Form layout passed to GenericViews.
                                     Default: True
    Automatic behaviour:
        - Renders partial_template_name on HTMX requests, full template_name otherwise.
        - If the form has a FormHelper, injects hx-post/hx-target for modal
          (when ``?_mid=`` is present) or form_action for full-page navigation.
        - model can be omitted if form_class defines Meta.model.
        - On save: HX-Refresh on HTMX requests, redirect to list-view-{slugname} otherwise.
    """

    template_name = "django_tables2/generic_form.html"
    partial_template_name = "django_tables2/generic_form_item.html"
    title_form = None
    auto_forms_buttons = True

    def get_template_names(self):
        if self.request.htmx:
            return [self.partial_template_name]
        return super().get_template_names()

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if not hasattr(form, "helper"):
            return form
        mid = self.request.GET.get("_mid") or self.request.POST.get("_mid")
        form_id = get_unique_id("form-")
        if mid:
            form.helper.attrs = {
                "id": form_id,
                "hx-post": f"{self.request.path}?_mid={mid}",
                "hx-target": f"#modal-{mid}-body",
                "hx-swap": "innerHTML",
            }
        else:
            form.helper.attrs = {"id": form_id}
            form.helper.form_action = self.request.path

        if self.auto_forms_buttons and getattr(form.helper, "layout", None) is not None:
            form.helper._usar_modal = bool(mid)
            if not mid and not getattr(form.helper, "back_url", None):
                try:
                    form.helper.back_url = reverse(f"list-view-{self.get_slugname()}")
                except Exception:
                    pass
            form.helper.layout.fields.append(get_form_buttons())

        return form

    def _get_model(self):
        if self.model:
            return self.model
        return getattr(getattr(self.form_class, "_meta", None), "model", None)

    def get_queryset(self):
        if self.model is None and self.queryset is None:
            model = self._get_model()
            if model:
                return model._default_manager.all()
        return super().get_queryset()

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.request.htmx:
            htmx_response = HttpResponse()
            htmx_response["HX-Refresh"] = "true"
            return htmx_response
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["partial_template_name"] = self.partial_template_name
        context["title_form"] = self.title_form
        return context

    def get_success_url(self):
        return reverse(f"list-view-{self.get_slugname()}")


class GenericCreateView(HtmxFormMixin, CreateView):
    """
    Required attributes:
        form_class  (ModelForm) Must define Meta.model (model is derived automatically).

    Auto-generated URL:
        {slugname}/crear/  →  name="create-view-{slugname}"

    Slugname: class name with "CreateView" suffix removed, lowercased.
    Example: ArticuloCreateView → "articulo"
    """

    @classmethod
    def get_slugname(cls):
        return cls.__name__.lower().removesuffix("createview")


class GenericUpdateView(HtmxFormMixin, UpdateView):
    """
    Required attributes:
        form_class  (ModelForm) Must define Meta.model (model and queryset are derived automatically).

    Auto-generated URL:
        {slugname}/<pk>/editar/  →  name="update-view-{slugname}"

    Slugname: class name with "UpdateView" suffix removed, lowercased.
    Example: ArticuloUpdateView → "articulo"
    """

    @classmethod
    def get_slugname(cls):
        return cls.__name__.lower().removesuffix("updateview")


class GenericDeleteView(DeleteView):
    """
    Generic DeleteView that always renders inside a Bootstrap modal.
    Shows confirmation with the object's ``__str__`` before deleting.

    Auto-registers via add_urls() / load_scotty_urls().

    Auto-generated URL:
        {slugname}/<pk>/eliminar/  →  name="delete-view-{slugname}"

    Slugname: class name with "DeleteView" suffix removed, lowercased.
    Example: ArticuloDeleteView → slugname="articulo"
    """

    template_name = "django_tables2/generic_delete_confirm.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["mid"] = self.request.GET.get("_mid") or self.request.POST.get("_mid")
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.request.htmx:
            htmx_response = HttpResponse()
            htmx_response["HX-Refresh"] = "true"
            return htmx_response
        return response

    def get_success_url(self):
        return reverse(f"list-view-{self.get_slugname()}")

    @classmethod
    def get_slugname(cls):
        """Return a slugname for the view URL."""
        return cls.__name__.lower().removesuffix("deleteview")


class DictTableView(ExportMixin, SingleTableView):
    template_name = "django_tables2/base_django_tables2_dict.html"
    show_export_xls = False
    show_filter_line = False

    # TODO: Test
    @classmethod
    def get_slugname(cls):
        """Return a slugname for the view URL."""
        trimed_view_name = cls.__name__.lower().removesuffix("view")
        return trimed_view_name

    def get_context_data(self, **kwargs):
        """Add the total unfiltered record count to the context."""
        # First, get the base context from the parent
        context = super().get_context_data(**kwargs)

        # Add control to show/hide bulk actions
        context["show_export_xls"] = self.show_export_xls
        context["show_filter_line"] = self.show_filter_line

        return context


def add_urls(views_modules: List) -> List:
    """Create urlpatterns for CottonTableView modules found in *views_modules*."""
    urlpatterns = []
    for module in views_modules:
        for name, cls in inspect.getmembers(module, inspect.isclass):
            if (
                name != "CottonTableView"
                and (issubclass(cls, CottonTableView) or issubclass(cls, DictTableView))
                and hasattr(cls, "as_view")
            ):
                trimed_view_name = cls.get_slugname()
                urlpatterns.append(
                    path(
                        f"{trimed_view_name}/",
                        cls.as_view(),
                        name=f"list-view-{trimed_view_name}",
                    )
                )
            if issubclass(cls, GenericDetailView):
                trimed_view_name = cls.get_slugname()
                urlpatterns.append(
                    path(
                        f"{trimed_view_name}/<int:pk>/",
                        cls.as_view(model=cls.model),
                        name=f"detail-view-{trimed_view_name}",
                    )
                )
            if name != "GenericCreateView" and issubclass(cls, GenericCreateView):
                trimed_view_name = cls.get_slugname()
                urlpatterns.append(
                    path(
                        f"{trimed_view_name}/crear/",
                        cls.as_view(),
                        name=f"create-view-{trimed_view_name}",
                    )
                )
            if name != "GenericUpdateView" and issubclass(cls, GenericUpdateView):
                trimed_view_name = cls.get_slugname()
                urlpatterns.append(
                    path(
                        f"{trimed_view_name}/<int:pk>/editar/",
                        cls.as_view(),
                        name=f"update-view-{trimed_view_name}",
                    )
                )
            if name != "GenericDeleteView" and issubclass(cls, GenericDeleteView):
                trimed_view_name = cls.get_slugname()
                urlpatterns.append(
                    path(
                        f"{trimed_view_name}/<int:pk>/eliminar/",
                        cls.as_view(),
                        name=f"delete-view-{trimed_view_name}",
                    )
                )
    return urlpatterns


def load_scotty_urls(app_name=None):
    """
    Auto-detect the current app from the calling module.
    Searches <app>/scotty/ for all .py modules and applies add_urls() to each.
    Returns a combined urlpatterns list.
    """
    if app_name is None:
        # --- 1. Detect where the function was called from ---
        caller_frame = inspect.stack()[1]
        caller_module = inspect.getmodule(caller_frame[0])
        caller_module_name = caller_module.__name__  # e.g. "mi_app.urls"
        # Derive the app name → "mi_app"
        app_name = caller_module_name.split(".")[0]

    # --- 2. Get the app package path ---
    app_module = importlib.import_module(app_name)
    app_path = os.path.dirname(app_module.__file__)
    scotty_dir = os.path.join(app_path, "scotty")

    collected_urls = []

    # --- 3. Find and load modules inside scotty/ ---
    if os.path.isdir(scotty_dir):
        for module_info in pkgutil.iter_modules([scotty_dir]):
            module_name = module_info.name
            if module_name == "__init__":
                continue

            full_module_path = f"{app_name}.scotty.{module_name}"

            modules_list = []
            try:
                module = importlib.import_module(full_module_path)
                modules_list.append(module)
            except Exception as err:
                logging.error(
                    f"[SCOTTY LOADER] Error importing {full_module_path} {err}"
                )
            collected_urls += add_urls(modules_list)

    return collected_urls