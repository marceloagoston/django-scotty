from crispy_forms.layout import HTML, Div, Submit


class CloseButton(HTML):
    """Renderizar boton Volver/Cerrar segun se use modal o no"""
    def render(self, form, context, **kwargs):
        usar_modal = getattr(form.helper, '_usar_modal', False)
        if usar_modal:
            html = '<button type="button" class="btn btn-outline-primary" data-bs-dismiss="modal">Cerrar</button>'
        else:
            back_url = getattr(form.helper, 'back_url', '#')
            html = f'<a href="{back_url}" class="btn btn-outline-primary">Volver</a>'
        self.html = html
        return super().render(form, context, **kwargs)


def get_form_buttons():
    return Div(
        CloseButton(''),
        Submit('submit', 'Guardar', css_class='btn btn-primary'),
        css_class='d-flex justify-content-between gap-2 mt-3',
    )
