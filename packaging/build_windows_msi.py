"""Build the per-user x64 MSI with the Windows CPython 3.10 MSI SDK."""
import argparse
from pathlib import Path
import uuid
import msilib
from msilib import schema, sequence

VERSION='1.4.10'
NAMESPACE=uuid.UUID('61cf2291-d460-4d8e-ba32-cc6d17fb477b')


def guid(key):
    return '{'+str(uuid.uuid5(NAMESPACE,key)).upper()+'}'


def msi_name(name, index, prefix):
    return f'{prefix}{index:07X}|{name}'


def build(payload, output):
    exe=payload/'ACE-DAW-Transfer.exe'
    if not exe.is_file() or output.exists() or not msilib.Win64:
        raise ValueError('Expected an x64 payload and a new output file.')
    db=msilib.init_database(str(output),schema,'ACE-DAW Transfer',guid('product-'+VERSION),VERSION,'Qing Audio')
    def add(table,*rows):
        msilib.add_data(db,table,list(rows))
    for table in ('AdminExecuteSequence','AdvtExecuteSequence','AdminUISequence','InstallExecuteSequence','InstallUISequence'):
        rows=[r for r in getattr(sequence,table) if r[0] not in ('ExitDialog','FatalError','UserExit','ErrorDlg')]
        rows=[(a,c,300 if a=='LaunchConditions' else n) for a,c,n in rows]
        add(table,*rows)
    upgrade=guid('upgrade')
    add('Property',('UpgradeCode',upgrade),('MSIFASTINSTALL','1'),('SecureCustomProperties','OLDPRODUCTS;NEWERPRODUCTS'))
    add('Upgrade',(upgrade,None,VERSION,None,1,None,'OLDPRODUCTS'),
        (upgrade,VERSION,None,None,2,None,'NEWERPRODUCTS'))
    add('LaunchCondition',('NOT NEWERPRODUCTS','A newer version of ACE-DAW Transfer is installed.'))
    add('Directory',('TARGETDIR',None,'SourceDir'),('LocalAppDataFolder','TARGETDIR','.'),
        ('ProgramsRoot','LocalAppDataFolder','Programs'),('INSTALLFOLDER','ProgramsRoot','ACEDAW|ACE-DAW Transfer'),
        ('ProgramMenuFolder','TARGETDIR','.'))
    add('Feature',('Main',None,'ACE-DAW Transfer','Offline project transfer',1,1,'INSTALLFOLDER',0))
    dirs={Path('.'):'INSTALLFOLDER'}
    cab=msilib.CAB('payload.cab')
    for i,p in enumerate(sorted(payload.rglob('*')),1):
        rel=p.relative_to(payload)
        if p.is_dir():
            ident=f'D{i}'
            add('Directory',(ident,dirs[rel.parent],msi_name(p.name,i,'D')))
            dirs[rel]=ident
            continue
        fid=f'F{i}'; comp=f'C{i}'; seq,_=cab.append(str(p),p.name,fid)
        add('Component',(comp,guid('component:'+rel.as_posix()),dirs[rel.parent],256,None,fid))
        add('FeatureComponents',('Main',comp))
        add('File',(fid,comp,msi_name(p.name,i,'F'),p.stat().st_size,None,None,512,seq))
        if p==exe:
            add('Shortcut',('StartMenu','ProgramMenuFolder','ACEDAW|ACE-DAW Transfer',comp,'[#'+fid+']',
                None,'ACE Studio, Cubase and Nuendo project transfer',None,None,None,1,'INSTALLFOLDER'))
    cab.commit(db)
    db.Commit()

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('payload',type=Path);ap.add_argument('msi',type=Path)
    args=ap.parse_args();build(args.payload.resolve(),args.msi.resolve())

