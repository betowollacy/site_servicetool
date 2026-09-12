# -*- coding: utf-8 -*-
"""Vincula automaticamente os servicos ativos sem API ao provedor,
por palavra-chave do titulo (nome da ferramenta + duracao + tipo).

Uso: python manage.py auto_link_services [--dry]
"""
import unicodedata

from django.core.management.base import BaseCommand

from core.models import ServiceList
from core.provider_api import auto_link_service


def norm(t):
    return unicodedata.normalize('NFKD', t or '').encode('ascii', 'ignore').decode().lower()


class Command(BaseCommand):
    help = 'Auto-integra servicos sem API ao provedor por palavra-chave do titulo.'

    def add_arguments(self, parser):
        parser.add_argument('--dry', action='store_true', help='So mostra o que faria.')

    def handle(self, *args, **options):
        dry = options['dry']
        linked, unmatched = [], []
        for s in ServiceList.objects.filter(status='Active').order_by('id'):
            if s.api_id and str(s.referenceid or '').strip():
                continue
            t = norm(s.title)
            if 'fonte' in t:
                continue  # rota manual declarada
            from core.provider_api import (
                _detect_duration, _detect_kind, _match_acceptable, find_remote_match,
            )
            remote, score, overlap = find_remote_match(s.title)
            if remote is not None:
                dur = _detect_duration(s.title)
                rdur = _detect_duration(remote.SERVICENAME)
                kind = _detect_kind(s.title)
                rkind = _detect_kind(remote.SERVICENAME)
                if not _match_acceptable(score, overlap, dur, rdur, kind, rkind):
                    remote = None
            if remote is not None:
                if not dry:
                    auto_link_service(s)
                linked.append((s.id, s.title, remote.referenceid, remote.SERVICENAME, score))
                self.stdout.write('LINKED id={} -> ref={} ({}) score={:.2f}'.format(
                    s.id, remote.referenceid, remote.SERVICENAME[:50], score))
            else:
                unmatched.append((s.id, s.title, score))
        self.stdout.write('--- RESUMO ---')
        self.stdout.write('vinculados: {}'.format(len(linked)))
        self.stdout.write('sem match confiavel: {}'.format(len(unmatched)))
        for sid, title, score in unmatched:
            self.stdout.write('UNMATCHED id={} score={:.2f} {}'.format(sid, score, title[:60]))
