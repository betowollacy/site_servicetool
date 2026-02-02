from django.shortcuts import render

# Create your views here.
def index(request):
    return render(request, 'index.html')

def aluguel(request):
    return render(request, 'aluguel.html')

def amt(request):
    return render(request, 'amt.html')

def unlock(request):
    return render(request, 'unlock.html')

def tsm(request):
    return render(request, 'tsm.html')

def tfm(request):
    return render(request, 'tfm.html')

def cheetah(request):
    return render(request, 'cheetah.html')

def dft(request):
    return render(request, 'dft.html')

def oct(request):
    return render(request, 'oct.html')

def kgpro(request):
    return render(request, 'kgpro.html')


def mdm(request):
    return render(request, 'mdm.html')

def black(request):
    return render(request, 'black.html')