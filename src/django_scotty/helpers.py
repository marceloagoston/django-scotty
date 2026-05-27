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
from django_scotty.form_helpers import get_form_buttons
from django.core.paginator import EmptyPage, Paginator
from django.db.models import QuerySet
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.safestring import SafeText
from django.views.generic import CreateView, DeleteView, DetailView, UpdateView
from django_filters.views import FilterView
from django_tables2.export.views import ExportMixin
from django_tables2.views import SingleTableMixin, SingleTableView
import django_tables2 as tables


class ActionTable(tables.Table):
    def __init__(self, *args, **kwargs):
        self.action_columns = kwargs.pop("available_actions", [])
        self.post_paginate_hook = kwargs.pop("post_paginate_hook", None)
        super().__init__(*args, **kwargs)

    acciones = tables.Column(verbose_name="Acciones", orderable=False, empty_values=())

    def get_ver_link(self, url):
        """Mostrar un link a una url con un boton de ver."""
        return SafeText(f'<a href="{url}" class="btn boton-ver"></a>')

    # TODO: Test
    def render_acciones(self, record):
        """Renderizar todas las acciones disponibles.
        Si es una sola en forma de botón, si es más de una
        como botones agrupados."""

        rendered_edit = SafeText("")
        if getattr(self, "updateview_class", None) is not None:
            try:
                edit_url = reverse(self.update_url_name, kwargs={"pk": record.pk})
                if getattr(self, "usar_modal", False):
                    modal_id = f"modal-{self.unique_id}"
                    rendered_edit = SafeText(
                        f'<button class="btn btn-warning btn-sm"'
                        f' hx-get="{edit_url}?_mid={self.unique_id}"'
                        f' hx-target="#{modal_id}-body"'
                        f' hx-swap="innerHTML"'
                        f' data-bs-toggle="modal"'
                        f' data-bs-target="#{modal_id}">Editar</button>'
                    )
                else:
                    rendered_edit = SafeText(
                        f'<button class="btn btn-warning btn-sm"'
                        f' hx-get="{edit_url}"'
                        f' hx-target="#main-content"'
                        f' hx-swap="innerHTML"'
                        f' hx-push-url="true">Editar</button>'
                    )
            except Exception as err:
                logging.error(f"[SCOTTY LOADER] Error mostrando el botón editar {err}")

        rendered_delete = SafeText("")
        if getattr(self, "deleteview_class", None) is not None:
            try:
                delete_url = reverse(self.delete_url_name, kwargs={"pk": record.pk})
                delete_mid = f"delete-{self.unique_id}"
                modal_id = f"modal-{delete_mid}"
                rendered_delete = SafeText(
                    f'<button class="btn btn-danger btn-sm ms-1"'
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
                    class=\"btn btn-primary\"
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
                            class="btn btn-primary dropdown-toggle"
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
        # Llamamos al método original primero
        super().paginate(*args, **kwargs)

        # Ahora que la paginación ocurrió, 'self.page' existe
        if self.page and self.post_paginate_hook:
            self.post_paginate_hook(self.page.object_list)


# TODO: Test
class PaginationFixMixin:
    """Mixin para manejar errores de paginación cuando se aplican filtros"""

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
    # Control para mostrar/ocultar "Acción sobre seleccionados"
    show_bulk_actions = True
    # Sistema unificado de botones de filtros
    available_filter_buttons = [
        "filtrar",
        "exportar_xls",
    ]

    # TASK: agegar permisos
    # Extra header buttons — list of {'name': str, 'url': str, 'label': str, 'order': int} dicts.
    # Items missing url or label are skipped.
    # Define <name>_method(self, request) -> dict to append extra query params to the url.
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
        # Sobreescribe get_table para pasar la instancia de la vista
        # "
        table = super().get_table(**kwargs)
        table.view = self  # Pasa la instancia de la vista a la tabla

        # Si hay definido hook de pre_render
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

    def _get_processed_links(self):
        """Procesa links para mostrarse en sección 'extra_links_actions'

        El proceso constade los siguientes pasos:
        1. itera sobre los items que tengan url y label definidos
        2. Evalua si el item no tiene redefinido el comportamiento {name}_method
            2.1 Esto se usa para agregarle queryparams o pk
            2.2 Si tiene se recrea la url agregando la pk y los queryparams
        3. Se agrega el link a "processed_links"
        4. Se lo ordena inversamente para mostrar el primero mas a la derecha
        usando el atributo "order"
        
        
        Si se define método personalizado para una url se re ajusta la
        url del item

        Args:
            - None
        Returns:
            - processed_links (dict): listado de links a renderizar
        """
        processed_links = []
        
        for link in self.extra_links_actions:
            if not link.get("url") or not link.get("label"):
                continue
            entry = dict(link)
            name = link.get("name", "")
            method = getattr(self, f"{name}_method", None) if name else None
            if callable(method):
                try:
                    extra_params = method(self.request)
                    if extra_params:
                        separator = "&" if "?" in entry["url"] else "?"
                        entry["url"] = f"{entry['url']}{separator}{urlencode(extra_params)}"
                except Exception:
                    pass
            processed_links.append(entry)

        processed_links.sort(key=lambda x: x.get("order", 0), reverse=True)

        return processed_links

    def get_context_data(self, **kwargs):
        """Agregamos el total de registros sin filtrar al contexto."""
        # Primero, obtenemos el contexto base de la clase padre
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

        # TASK: mandar a cte el default value
        orig_table.empty_text = self.table_empty_text if self.table_empty_text is not None else '- No hay datos para mostrar -'
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

        # Agregar control para mostrar/ocultar acciones masivas
        context["show_bulk_actions"] = self.show_bulk_actions

        # Sistema unificado de botones de filtros
        if (
            hasattr(self, "available_filter_buttons")
            and self.available_filter_buttons is not None
        ):
            context["show_action_buttons"] = self.available_filter_buttons
        else:
            # Fallback: construir botones basado en flags individuales
            buttons = []
            if hasattr(self, "show_filter_line") and self.show_filter_line:
                buttons.extend(["filtrar", "limpiar"])
            if hasattr(self, "show_export_xls") and self.show_export_xls:
                buttons.append("exportar_xls")
            context["show_action_buttons"] = buttons

        # Lógica unificada: cuando se incluye 'filtrar', automáticamente se incluye 'limpiar'
        action_buttons = context["show_action_buttons"]
        if "filtrar" in action_buttons and "limpiar" not in action_buttons:
            action_buttons = list(action_buttons) + ["limpiar"]
            context["show_action_buttons"] = action_buttons

        return context

    def get_export_filename(self, export_format):
        """Generar nombre de archivo basado en el nombre de la clase de la vista."""
        class_name = self.__class__.__name__.replace("View", "")
        filename = re.sub(r"[^\w\s-]", "", class_name.lower())
        filename = re.sub(r"[-\s]+", "_", filename)
        return f"{filename}.{export_format}"

    # TODO: Test
    @property
    def available_actions(self):
        """Devolver el short name de una acción, si existe."""
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
    # TODO: Agregar que sea posible aplicar toda la seleccion al queryset filtrado
    # completo con algún Flag.
    def post(self, request, *args, **kwargs):
        """Manejar las operaciones POST sobre los elementos seleccionados"""

        # La lista de los IDs de los checkboxes marcados
        # o si es una colunmna, la columna elegida
        if (pk := request.GET.get("pk")) is not None:
            action = request.GET.get("action")
            selected_pks = [pk]
        else:
            action = request.POST.get("action")
            selected_pks = request.POST.getlist("seleccionar")

        queryset_to_act_on: QuerySet = None

        if selected_pks:
            # Caso 1: Acción sobre los elementos seleccionados
            queryset_to_act_on = self.model.objects.filter(pk__in=selected_pks)
        elif "filter_query_string" in request.POST:
            # Caso 2: Acción sobre el QuerySet filtrado completo
            # Recreamos el QuerySet filtrado sin la paginación
            filter_params = parse_qs(request.POST["filter_query_string"])
            # Limpiamos los parámetros de paginación para obtener todo el QuerySet
            filter_params.pop("page", None)
            filter_params.pop("per_page", None)

            # Usamos el mismo filtro que en la vista GET
            filterset = self.filterset_class(
                filter_params, queryset=self.get_queryset()
            )
            queryset_to_act_on = filterset.qs

        # Ejecutar la acción si tenemos un QuerySet para procesar
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
                        # messages.warning(request, 'No se pudo realizar la accion')
                        pass
                except Exception:
                    # NO ejecutar la acción de nuevo si hay error
                    pass

            # FIXME: Mejorar esta lógica. De momento si una acción pide
            # hacer un redirect, no se ejecutaran las siguientes llamadas
            # a la misma si es en bulk. por lo que al no tener un criterio
            # se ejecutará el primer redirect.
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
        """Devolver un slugname para la URL de la vista."""
        trimed_view_name = cls.__name__.lower().removesuffix("view")
        return trimed_view_name

    # TASK: ver si no conviene ya tener un metodo que retorne el url_name


def generar_id_valido(base_id):
    """
    Genera un ID válido para HTML y CSS a partir de una cadena base.

    Reemplaza los caracteres no válidos como el punto (.) por guiones (-).
    Asegura que el ID comience con una letra si la cadena base comienza con un número.
    """
    # 1. Reemplazar caracteres problemáticos, como el punto, por guiones.
    id_sanitizado = base_id.replace(".", "-")

    # 2. Asegurarse de que el ID comience con una letra.
    #    Si el primer caracter es un número, le agregamos un prefijo.
    if id_sanitizado and id_sanitizado[0].isdigit():
        id_valido = f"id-{id_sanitizado}"
    else:
        id_valido = id_sanitizado

    return id_valido


# TODO: Test
def get_unique_id(prefix=""):
    """Generar un ID único con un prefijo opcional"""
    component_id = uuid.uuid1().__str__().replace("-", "")[2:8]
    sanitized_id = generar_id_valido(component_id)
    return f"{prefix}{sanitized_id}"


class GenericDetailView(DetailView):
    """
    Una DetailView genérica que automáticamente genera una lista de campos y valores
    del objeto para ser renderizados por una plantilla.
    Si se desea, se puede personalizar el detalle sobreescibiendo el template
    """

    # Apuntamos a nuestra plantilla genérica
    template_name = "django_tables2/generic_detail.html"

    # Opcional: define campos que nunca quieres mostrar
    exclude_fields = ["id"]

    def get_context_data(self, **kwargs):
        """
        Sobrescribimos este método para inyectar nuestra lista de campos en el contexto.
        """
        context = super().get_context_data(**kwargs)
        instance = context["object"]

        field_list = []
        # Iteramos sobre todos los campos definidos en el modelo
        for field in instance._meta.get_fields():
            # Many to many por ahora no manejamos
            if not field.concrete or field.many_to_many:
                continue

            # Sacamos los campos excluidos
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

        # Agregamos la lista y un título al contexto
        context["field_list"] = field_list
        context["title"] = (
            f"Detalle de {instance._meta.verbose_name.capitalize()} {instance.id}"
        )
        return context

    # TODO: Test
    @classmethod
    def get_slugname(cls):
        """Devolver un slugname para la URL de la vista."""
        trimed_view_name = cls.__name__.lower().removesuffix("detailview")
        return trimed_view_name


class HtmxFormMixin:
    """
    Mixin compartido por GenericCreateView y GenericUpdateView.

    Atributos opcionales:
        partial_template_name (str)  Template del fragmento cargado vía HTMX.
                                     Default: "django_tables2/generic_form_item.html".
        title_form            (str)  Título dentro del formulario. Default: None.
        auto_forms_buttons    (bool) Genera botones "Submit" (Guardar) y Cerrar/Volver
                                     automaticamente según se haya pedido que los forms
                                     se rendericen en modal o no

                                     Hace append de los botones al final del layout del
                                     Form que se le haya pasado a las GenericViews
                                     Default: True
    Comportamiento automático:
        - Renderiza partial_template_name en requests HTMX, template_name completo en el resto.
        - Si el form tiene FormHelper, inyecta los atributos hx-post/hx-target para modal
          (cuando llega ?_mid=) o form_action para navegación full-page.
        - model puede omitirse si form_class define Meta.model.
        - Tras guardar: HX-Refresh en requests HTMX, redirect a list-view-{slugname} en el resto.
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
    Atributos obligatorios:
        form_class  (ModelForm) Debe definir Meta.model (model se deriva automáticamente).

    URL generada automáticamente:
        {slugname}/crear/  →  name="create-view-{slugname}"

    Slugname: nombre de clase sin sufijo "CreateView" en minúsculas.
    Ejemplo: ArticuloCreateView → "articulo"
    """

    @classmethod
    def get_slugname(cls):
        return cls.__name__.lower().removesuffix("createview")


class GenericUpdateView(HtmxFormMixin, UpdateView):
    """
    Atributos obligatorios:
        form_class  (ModelForm) Debe definir Meta.model (model y queryset se derivan automáticamente).

    URL generada automáticamente:
        {slugname}/<pk>/editar/  →  name="update-view-{slugname}"

    Slugname: nombre de clase sin sufijo "UpdateView" en minúsculas.
    Ejemplo: ArticuloUpdateView → "articulo"
    """

    @classmethod
    def get_slugname(cls):
        return cls.__name__.lower().removesuffix("updateview")


class GenericDeleteView(DeleteView):
    """
    DeleteView genérica que siempre se renderiza dentro de un modal Bootstrap.
    Muestra confirmación con el __str__ del objeto antes de eliminar.

    Se registra automáticamente en las URLs via add_urls() / load_scotty_urls().

    URL generada automáticamente:
        {slugname}/<pk>/eliminar/  →  name="delete-view-{slugname}"

    El slugname se deriva del nombre de la clase removiendo el sufijo "DeleteView".
    Ejemplo: ArticuloDeleteView → slugname="articulo"
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
        """Devolver un slugname para la URL de la vista."""
        return cls.__name__.lower().removesuffix("deleteview")


class DictTableView(ExportMixin, SingleTableView):
    template_name = "django_tables2/base_django_tables2_dict.html"
    show_export_xls = False
    show_filter_line = False

    # TODO: Test
    @classmethod
    def get_slugname(cls):
        """Devolver un slugname para la URL de la vista."""
        trimed_view_name = cls.__name__.lower().removesuffix("view")
        return trimed_view_name

    def get_context_data(self, **kwargs):
        """Agregamos el total de registros sin filtrar al contexto."""
        # Primero, obtenemos el contexto base de la clase padre
        context = super().get_context_data(**kwargs)

        # Agregar control para mostrar/ocultar acciones masivas
        context["show_export_xls"] = self.show_export_xls
        context["show_filter_line"] = self.show_filter_line

        return context


def add_urls(views_modules: List) -> List:
    """Crear urlpatterns para módulos de CottonTableView presentes en views_modules."""
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
    Auto-detecta la app actual basándose en el módulo que lo llama.
    Busca dentro de <app>/scotty/ todos los módulos .py y les aplica add_urls().
    Devuelve un unico urlpatterns combinando todo.
    """
    if app_name is None:
        # --- 1. Detectar desde dónde fue llamada la función ---
        caller_frame = inspect.stack()[1]
        caller_module = inspect.getmodule(caller_frame[0])
        caller_module_name = caller_module.__name__  # ejemplo: "mi_app.urls"
        # Derivar el nombre de la app -> "mi_app"
        app_name = caller_module_name.split(".")[0]

    # --- 2. Obtener ruta del paquete de la app ---
    app_module = importlib.import_module(app_name)
    app_path = os.path.dirname(app_module.__file__)
    scotty_dir = os.path.join(app_path, "scotty")

    collected_urls = []

    # --- 3. Buscar y cargar módulos dentro de scotty/ ---
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
                    f"[SCOTTY LOADER] Error importando {full_module_path} {err}"
                )
            collected_urls += add_urls(modules_list)

    return collected_urls