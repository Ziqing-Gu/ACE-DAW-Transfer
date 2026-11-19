"""Interchange contracts and failure cases for opaque VST3 state transfer."""
import copy
import json
import struct
import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as E
import native_xml as n
from plugin_state import (cid, make_preset, parse_preset, envelope, from_envelope,
                          candidate_plugin, restore_chain, state_summary)
from plugin_xml import write_inserts, read_inserts
from archive_reader import Archive

CID='0123456789ABCDEF0123456789ABCDEF'


class PluginTests(unittest.TestCase):
    def plugin(self):
        data=make_preset(CID,{b'Comp':bytes(range(256))*40,b'Cont':b'controller'})
        return {'name':'Test effect','format':'vst3','slot':0,'enabled':True,'bypassed':False,
                'typeId':'test-effect','preset':envelope(data),**state_summary(data)}

    def test_container_roundtrip_and_corruption(self):
        data=from_envelope(self.plugin()['preset'])
        self.assertEqual(parse_preset(data),(CID,{b'Comp':bytes(range(256))*40,b'Cont':b'controller'}))
        for bad in (data[:-1],data[:8]+b'Z'*32+data[40:],data[:40]+struct.pack('<q',1)+data[48:]):
            with self.assertRaises(ValueError):parse_preset(bad)
        damaged=bytearray(data);offset=struct.unpack_from('<q',data,40)[0]
        struct.pack_into('<q',damaged,offset+12,0)
        with self.assertRaises(ValueError):parse_preset(bytes(damaged))
        with self.assertRaises(ValueError):from_envelope({**envelope(data),'count':1})

    def test_chain_order_state_and_native_binary_lines(self):
        track=n.track('Plugin track',0,[])
        folder=track.find('.//member[@name="InsertFolder"]')
        plugins=[self.plugin() for i in range(3)]
        for i,p in enumerate(plugins):p['name']+=str(i)
        plugins[1]['bypassed']=True;plugins[2]['enabled']=False
        warnings=write_inserts(folder,plugins)
        decoded=read_inserts(folder)
        self.assertEqual([p['name'] for p in decoded],[p['name'] for p in plugins])
        self.assertEqual([p['enabled'] for p in decoded],[True,False,False])
        self.assertTrue(warnings)
        for a,b in zip(decoded,plugins):self.assertEqual(a['chunks'],b['chunks'])
        for b in folder.iter('bin'):
            self.assertTrue(all(len(line.strip())<=64 for line in (b.text or '').splitlines()))
        self.assertFalse(folder.findall('.//int[@name="BypassTag"]'))

    def test_missing_plugin_and_wrong_cid_stop_before_import(self):
        p=self.plugin()
        with self.assertRaises(ValueError):candidate_plugin(p,[])
        calls=[]
        def call(group,sub,**kw):
            calls.append(sub)
            if sub=='add':return {'insert':{'slot':0}}
            if sub=='export-preset':return {'blob':envelope(make_preset('F'*32,{b'Comp':b'x'}))}
            raise AssertionError(sub)
        with self.assertRaisesRegex(ValueError,'CID'):restore_chain(call,'track',[p],[{'name':p['name'],'typeId':'test-effect','format':'vst3'}])
        self.assertNotIn('import-preset',calls)

    def test_plugin_parameter_drift_is_not_success(self):
        p=self.plugin();p['parameters']=[{'paramId':'1','name':'Window','value':1.}]
        def call(group,sub,**kw):
            if sub=='add':return {'insert':{'slot':0}}
            if sub=='export-preset':return {'blob':p['preset']}
            if sub=='import-preset':return {}
            if sub=='get-params':return {'params':[{'paramId':'1','name':'Window','value':0.}]}
            raise AssertionError(sub)
        with self.assertRaisesRegex(ValueError,'Window'):restore_chain(call,'track',[p],[{'name':p['name'],'typeId':'test-effect','format':'vst3'}])

    def test_post_fader_and_custom_routing_rejected(self):
        folder=n.track('Track',0,[]).find('.//member[@name="InsertFolder"]')
        write_inserts(folder,[self.plugin()])
        folder.find('int[@name="SeparationPosition"]').set('value','0')
        with self.assertRaisesRegex(ValueError,'推子后'):read_inserts(folder)
        folder.find('int[@name="SeparationPosition"]').set('value','14')
        folder.find('.//member[@name="Patcher"]//int[@name="ID"]').set('value','7')
        with self.assertRaisesRegex(ValueError,'路由'):read_inserts(folder)

    def test_automation_requires_explicit_static_option(self):
        root=n.document([n.track('Static values',0,[])])
        track=root.find('list[@name="track"]/obj')
        auto=E.SubElement(track,'obj',{'class':'MAutoListNode','name':'Automation','ID':'900000'})
        points=E.SubElement(auto,'list',name='Events',type='obj')
        E.SubElement(points,'item',value='0.5')
        out=Path(tempfile.mkdtemp(prefix='plugin-regression-'))/'automation.xml'
        E.ElementTree(root).write(out,encoding='utf-8')
        self.assertTrue(any('自动化' in e for e in Archive(out).errors))
        archive=Archive(out,static_only=True)
        self.assertFalse(archive.errors,archive.errors)
        self.assertTrue(any('自动化' in e for e in archive.warnings))


if __name__=='__main__':unittest.main()
