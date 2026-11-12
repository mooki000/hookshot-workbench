#!/usr/bin/env python3
"""Build adapter for regress.py; machine paths, accepted SDK and diagnostic patches are in local scenario JSON."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

HARN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HARN if (HARN / 'hs_names.py').is_file() else HARN / 'harness'))
from hs_names import ALL_SETTINGS, PROJECTS, NameError, Names, build_env, checkout_names, copy_names


def copy(source,dest):
    subprocess.run(['cp','-a','--reflink=always',str(source),str(dest)],check=True)


def profile_inputs(directory, names, files=None):
    """Keep source filenames; map only known project files into the selected checkout."""
    directory = Path(directory)
    if files is None:
        profiles = [Names(project).profile for project in PROJECTS
                    if (directory / Names(project).profile).is_file()]
        if len(profiles) != 1:
            raise NameError('profile source must identify exactly one project: ' + str(directory))
        files = [profiles[0], 'hot_funcs.txt', 'hot_order.txt']
    inputs = [(directory / name, names.project_file(name)) for name in files]
    targets = [target for _, target in inputs]
    if len(set(targets)) != len(targets):
        raise NameError('profile source filenames map to the same destination')
    return inputs


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('worktree','commit','output','lift-key','build-kind','scenario'): p.add_argument('--'+key,required=True)
    p.add_argument('--donor-worktree',default=''); p.add_argument('--reverts',default='')
    a=p.parse_args(); w=Path(a.worktree); out=Path(a.output); scenario=json.loads(Path(a.scenario).read_text()); c=scenario['build']
    e=os.environ.copy()
    for k in ('HSGATE_SLOT_DIR','HSGATE_INTEL_SLOT_DIR','HSGATE_SLOT_TEST','HSGATE_SLOT_NODESKTOP','HSGATE_IN_GATE'): e.pop(k,None)
    spec=importlib.util.spec_from_file_location('regress_build_support',Path(__file__).with_name('regress.py'))
    support=importlib.util.module_from_spec(spec); spec.loader.exec_module(support)
    # An explicitly supplied already-tested binary is immutable; preserve its provenance, don't replace it in place.
    prebuilt=c.get('prebuilt',{}).get(a.commit) if not a.reverts else None
    if prebuilt:
        manifest=json.loads(Path(prebuilt).read_text())
        assert manifest.get('base',manifest.get('commit'))==a.commit and Path(manifest['copy']).is_dir()
        copy_names(Path(manifest['copy']))
        assert manifest.get('sha256'), 'prebuilt identity needs binary checksums'
        for name,digest in manifest['sha256'].items():
            with (Path(manifest['copy'])/name).open('rb') as f: assert hashlib.file_digest(f,'sha256').hexdigest()==digest
        support.save(out/'build.json',{'copy':manifest['copy'],'commit':a.commit,'lift_key':a.lift_key,'prebuilt':prebuilt})
        return
    names=checkout_names(w)
    game=Path(c['game_root'])/('.hookshot-h-regress-'+out.parent.parent.name+'-'+out.name)
    assert not game.exists(), 'new game copy required'
    donor=Path(a.donor_worktree) if a.donor_worktree else None
    donor_game=None
    if donor:
        metadata=donor/'work/regress-build.json'
        if metadata.exists():
            m=json.loads(metadata.read_text()); assert m['lift_key']==a.lift_key; donor_game=Path(m['copy'])
        else:
            # A prebuilt endpoint did not build in the engine-created worktree. Find its original source via config.
            donor=None
    if not donor:
        for known in c.get('donors',[]):
            candidate=Path(known['worktree'])
            if known.get('completion_file') and known['completion_marker'] not in Path(known['completion_file']).read_text(): continue
            commit=support.git(candidate,'rev-parse','HEAD')
            if support.lift_key(candidate,commit)==a.lift_key:
                assert not support.git(candidate,'diff','HEAD','--','nx2',':(exclude)nx2/build.py','third_party/a64-kit','host'), 'donor lift inputs dirty'
                donor=candidate; donor_game=Path(known['copy']); break
    (w/'work/build').mkdir(parents=True,exist_ok=True)
    if donor:
        donor_names=checkout_names(donor)
        assert donor_game and donor_game.is_dir()
        if donor_names.executable != names.executable or copy_names(donor_game).executable != names.executable:
            raise NameError('donor and selected checkout have different project identities')
        source=donor/'work/fast' if (donor/'work/fast/sa/gen').is_dir() else donor/'work/build'
        assert copy_names(source/'sa/build').executable == names.executable and (source/'sa/gen').is_dir(), 'donor unfinished'
        copy(source,w/'work/fast')
        copy(source/donor_names.profile,w/'work/build'/names.profile)
        game.mkdir()
        for src in donor_game.iterdir():
            if src.name in ('runs',*ALL_SETTINGS,'mods'): continue
            copy(src,game/src.name)
    else:
        for source,name in profile_inputs(c['profile_dir'],names,c.get('profile_files')):
            assert source.is_file(), 'missing release profile input'
            copy(source,w/'work'/name)
    # Patches are explicitly selected by the local caller. Fail on conflicts rather than silently dropping instruments.
    for patch in c.get('patches',[]):
        if 'commits' in patch and a.commit not in patch['commits']: continue
        subprocess.run(['git','apply',str(Path(patch['path']).resolve())],cwd=w,check=True)
    for item in c.get('extra_files',[]):
        dest=w/item['destination']; assert not dest.exists(); copy(Path(item['source']),dest)
    e.update(RX_CPM=c['cpm'],RX_RELEASE='1')
    suffix_values={'NGX_SDK':c['ngx_sdk']} if c.get('ngx_sdk') else {}
    python=c['python']; gate=c.get('build_gate',str(Path.home()/'Games/hs-build-slot'))
    if donor:
        e.update(OOT_WORK=str(w/'work/fast'),RX_PGO=str(w/'work/build'/names.profile))
        argv=[gate,python,'-I','-u','nx2/build.py','--standalone','--jobs',str(c.get('jobs',16))]
    else:
        suffix_values.update(LIFT_JOBS=str(c.get('lift_jobs',8)),KEEP_BUILD_FILES='1')
        argv=['flock',c.get('full_build_lock','/tmp/hookshot-full-build.lock'),gate,python,'-I','-u','nx2/make_game.py',
              '--romfs',c['romfs'],'--exefs',c['exefs'],'--out',str(game),'--work',str(w/'work/build'),
              '--jobs',str(c.get('jobs',16)),'--no-prepare-shaders','--log',str(out/'make-game.log')]
    e.update(build_env(w,suffix_values))
    support.save(out/'command.json',{'argv':argv,'donor':str(donor) if donor else None,'started':support.date()})
    with (out/'compile.log').open('w') as log: subprocess.run(argv,cwd=w,env=e,stdout=log,stderr=subprocess.STDOUT,check=True)
    if donor:
        for name in (names.executable,names.renderer): copy(w/'work/fast/sa/build'/name,game/name)
    else:
        assert '@@done '+str(game) in (out/'make-game.log').read_text(), 'full build did not finish'
    if copy_names(game).executable != names.executable:
        raise NameError('built copy and selected checkout have different project identities')
    result={'copy':str(game),'commit':a.commit,'lift_key':a.lift_key,'finished':support.date(),'sha256':{}}
    for name in (names.executable,names.renderer):
        with (game/name).open('rb') as f: result['sha256'][name]=hashlib.file_digest(f,'sha256').hexdigest()
    import re   # (the build's own stamp, left beside the binary: the run adapter requires it in every run's log of this copy)
    logs=''.join(p.read_text(errors='replace') for p in (out/'compile.log',out/'make-game.log') if p.exists())
    stamp=re.search(r'^version: .*\(source ([0-9a-f]+)\)',logs,re.M)
    (game/'regress-source.txt').unlink(missing_ok=True)   # (a donor copy's stamp is not this build's)
    if stamp:
        (game/'regress-source.txt').write_text(stamp.group(1)+'\n'); result['source']=stamp.group(1)
    support.save(out/'build.json',result); support.save(w/'work/regress-build.json',result)


if __name__=='__main__': main()
