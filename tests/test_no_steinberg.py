"""The shipping routes must never use a Steinberg GUI as a converter."""
import ast
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app
from ace_short_events import marker_ticks


class DirectFileRoutes(unittest.TestCase):
    def test_saved_point_marker_survives_tick_and_time_minimum(self):
        self.assertEqual(marker_ticks({'duration':0},120),16)
        self.assertGreaterEqual(marker_ticks({'duration':0},1000)/8000,.011)

    def test_application_import_graph_has_no_host_controller(self):
        directory=Path(app.__file__).parent
        pending=['app']
        visited=set()
        while pending:
            name=pending.pop()
            if name in visited:continue
            visited.add(name)
            source=directory/(name+'.py')
            if not source.exists():continue
            for node in ast.walk(ast.parse(source.read_text(encoding='utf8'))):
                if isinstance(node,ast.Import):pending.extend(i.name for i in node.names)
                if isinstance(node,ast.ImportFrom) and node.module:pending.append(node.module)
        self.assertFalse(visited.intersection({'host_ui','host_export','host_native','ace_bridge','ace_insert_transfer','frida'}))
        self.assertTrue({'native_events','native_mixer','offline_native_writer'}<=visited)

    def test_fresh_ace_keeps_xml_when_native_cannot_be_created(self):
        from native_projects import write_native
        with TemporaryDirectory() as tmp:
            folder=Path(tmp)
            xml=folder/'fresh.xml'
            xml.write_text('<tracklist2/>',encoding='utf8')
            ace=folder/'fresh.acep'
            ace.write_bytes(b'fixture')
            with patch('subprocess.Popen',side_effect=AssertionError('unexpected process')):
                with self.assertRaisesRegex(ValueError,'速度或调色板'):
                    write_native(dict(xml=str(xml),source_project=str(ace),native_layout=[]),'.cpr',lambda _:None)
            self.assertTrue(xml.is_file())
            self.assertFalse(xml.with_suffix('.cpr').exists())
