from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('aluguel/', views.aluguel, name='aluguel'),
    path('carrinho/', views.carrinho, name='carrinho'),
    path('produto/<int:produto_id>/', views.produto_selecionado, name='produto_selecionado'),


]
