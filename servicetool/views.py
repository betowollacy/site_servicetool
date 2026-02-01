from django.shortcuts import render

# Create your views here.
def index(request):
    return render(request, 'index.html')

def aluguel(request):
    return render(request, 'aluguel.html')

def carrinho(request):
    return render(request, 'carrinho.html')

# views.py
from django.shortcuts import render, get_object_or_404
from .models import Produto

def produto_selecionado(request, produto_id):
    produto = get_object_or_404(Produto, id=produto_id)
    return render(request, 'produto_selecionado.html', {'produto': produto})
