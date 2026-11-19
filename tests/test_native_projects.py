"""Regression checks for host save failures and native project container headers."""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from native_projects import read_header, write_native


def project_bytes(magic, host):
    width = {b'RIFF': 4, b'RIF2': 8}[magic]
    body = b'ROOT' + host.encode('ascii') + bytes(1)
    return magic + len(body).to_bytes(width, 'big') + b'NUND' + body


class ProjectFormatTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.xml = Path(self.directory.name) / '工程.xml'
        self.xml.write_text('<tracklist2/>', encoding='utf-8')

    def test_recognizes_both_containers_and_rejects_truncation(self):
        project = self.xml.with_suffix('.npr')
        for fmt in (b'RIFF', b'RIF2'):
            raw = project_bytes(fmt, 'Nuendo')
            project.write_bytes(raw)
            self.assertEqual(read_header(project), raw)
            project.write_bytes(raw[:-1])
            with self.assertRaises(ValueError):
                read_header(project)

    def test_rejects_wrong_host_and_preserves_interchange(self):
        def save_project(xml, destination, log, *, source_bpm):
            destination.write_bytes(project_bytes(b'RIF2', 'Nuendo'))
        backend = types.SimpleNamespace(save_project=save_project)
        with patch.dict(sys.modules, host_native=backend):
            with self.assertRaises(ValueError):
                write_native({'xml': str(self.xml)}, '.cpr', lambda _: None)
        report = json.loads((self.xml.parent / '转换结果.json').read_text(encoding='utf-8'))
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(self.xml.read_text(encoding='utf-8'), '<tracklist2/>')

    def test_nondefault_bpm_is_the_active_tempo_in_the_native_seed(self):
        from native_document import assemble
        from native_inventory import read_project_timing
        for bpm in (92, 126.5):
            path = self.xml.with_name(f'{bpm}.npr')
            path.write_bytes(assemble(path, [], [], 1200, bpm, []))
            settings = read_project_timing(path)
            self.assertAlmostEqual(settings['bpm'], bpm, places=4)
            self.assertEqual(settings['sample_rate'], 48000)
