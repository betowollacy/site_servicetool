from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('aluguel/', views.aluguel, name='aluguel'),
    path('amt/', views.amt, name='amt'),
    path('tsm/', views.tsm, name='tsm'),
    path('tfm/', views.tfm, name='tfm'),
    path('mdm/', views.mdm, name='mdm'),
    path('oct/', views.oct, name='oct'),
    path('unlock/', views.unlock, name='unlock'),
    path('dft/', views.dft, name='dft'),
    path('cheetah/', views.cheetah, name='cheetah'),
    path('kgpro/', views.kgpro, name='kgpro'),
    path('black/', views.black, name='black'),
    #path('produto/<int:produto_id>/', views.produto_selecionado, name='produto_selecionado'),


]
