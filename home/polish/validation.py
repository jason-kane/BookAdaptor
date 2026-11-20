"""Syntax checks and isolated before/after validation for proposed source edits."""
from pathlib import Path


def syntax_check(path, content):
    if path.startswith('home/polish/prompts/') and path.endswith('.md'):
        from .prompt_profiles import parse_profile
        parse_profile(content,path)
    if Path(path).suffix in ('.py', '.pyi'):
        try:
            compile(content, path, 'exec', dont_inherit=True)
        except (SyntaxError, ValueError) as error:
            raise ValueError(f"Python syntax validation failed in {path}:{getattr(error, 'lineno', '?')}: {getattr(error, 'msg', str(error))}") from error

import ast
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import uuid

from flask import current_app

CODE_SUFFIXES = {'.py', '.pyi', '.js', '.cjs', '.mjs', '.html', '.css', '.json', '.toml', '.yaml', '.yml'}


class ValidationFailure(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__(report.get('message', 'Code validation failed.'))


def relevant_tests(root, name):
    path = Path(name)
    if path.name.startswith('test_') and path.suffix in ('.py', '.cjs'):
        return [name]
    module = name.removeprefix('src/').rsplit('.', 1)[0].replace('/', '.')
    selected = []
    candidates = list((root/'tests').rglob('test_*.py')) + list((root/'tests').glob('test_*.cjs'))
    if (root/'src/artifact_editor').is_dir():
        candidates += list((root/'src/artifact_editor').rglob('test_*.py'))
    for test in sorted(set(candidates)):
        relative = test.relative_to(root).as_posix()
        if '/ui/' in relative or test.is_symlink():
            continue
        text = test.read_text(errors='replace')
        matches = name in text or bool(re.search(r'(?<![\w.])'+re.escape(module)+r'(?![\w.])',text))
        if test.suffix == '.py':
            try:
                for node in ast.walk(ast.parse(text)):
                    if isinstance(node, ast.Import):
                        matches |= any(alias.name == module for alias in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        matches |= node.module == module or any(node.module+'.'+alias.name == module for alias in node.names)
            except SyntaxError:
                continue
        if matches:
            selected.append(relative)
    preferred = {f'test_{path.stem}.py', f'test_{path.stem}_{path.parent.name}.py',
                 f'test_{path.parent.name}_{path.stem}.py'}
    focused = [test for test in selected if Path(test).name in preferred]
    return focused or selected


# Trusted harness passed as Python -c, separate from the proposed checkout.
RUNNER = r'''
import compileall, json, os, pathlib, shutil, subprocess, sys, tempfile
root=pathlib.Path('/workspace')
data=json.loads((root/'.polish-validation.json').read_text())

def command(args,cwd):
    try:
        result=subprocess.run(args,cwd=cwd,capture_output=True,text=True,timeout=120)
        return {'command':args,'passed':result.returncode==0,'returncode':result.returncode,
                'output':(result.stdout+'\n'+result.stderr)[-8000:]}
    except subprocess.TimeoutExpired:
        return {'command':args,'passed':False,'output':'Timed out after 120 seconds.'}

def run(phase,content):
    case=pathlib.Path('/tmp')/phase
    shutil.copytree(root,case)
    file=case/data['path']
    if content is None:
        if file.exists(): file.unlink()
    else:
        file.parent.mkdir(parents=True,exist_ok=True)
        file.write_bytes(content.encode('utf-8'))
    # Test data/configuration never points into a host library or another service.
    config=case/'src/configuration.toml'
    config.parent.mkdir(parents=True,exist_ok=True)
    config.write_text('LIBRARY_DIR='+json.dumps(str(case/'test-data'))+'\nSRC_DIR='+json.dumps(str(case/'src'))+'\n')
    envpath=str(case/'src')+os.pathsep+str(case)
    os.environ['PYTHONPATH']=envpath
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    os.environ['HOME']='/tmp'
    checks=[]
    suffix=file.suffix
    if content is not None:
        try:
            if suffix in ('.py','.pyi'):
                compile(content,data['path'],'exec',dont_inherit=True)
                checks.append(command([sys.executable,'-m','ruff','check','--isolated','--select','E9,F821,F822,F823',data['path']],case))
            elif suffix in ('.js','.mjs','.cjs'):
                checks.append(command(['node','--check',data['path']],case))
            elif suffix=='.html':
                import jinja2
                jinja2.Environment().parse(content)
            elif suffix=='.css':
                import tinycss2
                def errors(items):
                    for item in items:
                        if item.type=='error': raise ValueError(item.message)
                        for attr in ('content','arguments','prelude'):
                            if getattr(item,attr,None): errors(getattr(item,attr))
                errors(tinycss2.parse_stylesheet(content))
            elif suffix=='.json': json.loads(content)
            elif suffix=='.toml':
                import tomllib
                tomllib.loads(content)
            elif suffix in ('.yaml','.yml'):
                import yaml
                yaml.safe_load(content)
        except Exception as error:
            checks.append({'command':['syntax',data['path']],'passed':False,'output':str(error)})
    py=[p for p in data['tests'] if p.endswith('.py') and (case/p).is_file()]
    js=[p for p in data['tests'] if p.endswith('.cjs') and (case/p).is_file()]
    if not checks or all(c['passed'] for c in checks):
        if py: checks.append(command([sys.executable,'-m','pytest','-q','--tb=short','--disable-warnings','--maxfail=3','-p','no:cacheprovider',*py],case))
        if js: checks.append(command(['node','--test',*js],case))
    return {'passed':bool(checks) and all(c['passed'] for c in checks),'checks':checks}

report={'tests':data['tests'],'baseline':run('baseline',data['before']),'candidate':run('candidate',data['after'])}
report['passed']=report['baseline']['passed'] and report['candidate']['passed']
report['retryable']=report['baseline']['passed'] and not report['candidate']['passed']
report['message']='Code checks passed before and after the diff.' if report['passed'] else ('Baseline checks failed; proposal cannot be verified.' if not report['baseline']['passed'] else 'Proposed code failed validation.')
print('POLISH_VALIDATION_RESULT '+json.dumps(report))
'''


def snapshot(root, target, data):
    result = subprocess.run(['git','-c',f'safe.directory={root.resolve()}','ls-files','--cached','--others','--exclude-standard','-z'],
                            cwd=root,capture_output=True,check=True,timeout=15)
    skip = {'.git','.aws','.codex','.agents','.venv','venv','__pycache__','active','active-test','comfy',
            'LLM_CACHE','htmlcov','coverage','tmp','dot-cache','dot-config','dot-local','src_backup','src_archive','node_modules','fontawesome7'}
    allowed = CODE_SUFFIXES | {'.md','.txt','.svg','.png','.jpg','.ico'}
    stream = tempfile.TemporaryFile()
    total = 0
    with tarfile.open(fileobj=stream,mode='w') as archive:
        def add(path, relative):
            nonlocal total
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root): return
            size=path.stat().st_size
            if size>4_000_000: return
            total+=size
            if total>200_000_000: raise ValueError('Validation snapshot exceeds the size limit.')
            def permissions(info):
                info.uid=info.gid=0
                info.uname=info.gname='root'
                info.mode=0o755 if info.mode & 0o111 else 0o644
                return info
            archive.add(path,arcname=relative,recursive=False,filter=permissions)
        for name in sorted(set(result.stdout.decode().split('\0')) | {target}):
            path=Path(name)
            if not name or path.is_absolute() or '..' in path.parts or any(part in skip for part in path.parts): continue
            if path.suffix not in allowed or any(word in path.name.lower() for word in ('secret','credential','password')): continue
            add(root/path,name)
        if any(test.endswith('.cjs') for test in data['tests']):
            for path in (root/'node_modules').rglob('*'):
                add(path,path.relative_to(root).as_posix())
        contents=json.dumps(data).encode()
        info=tarfile.TarInfo('.polish-validation.json');info.size=len(contents)
        archive.addfile(info,io.BytesIO(contents))
    stream.seek(0)
    return stream


def docker_validation(root, path, before, after, tests):
    container=None
    archive=snapshot(root,path,{'path':path,'before':before,'after':after,'tests':tests})
    try:
        result=subprocess.run(['docker','create','--runtime','runc','--network','none','--memory','2g','--cpus','2',
            '--pids-limit','256','--cap-drop','ALL','--security-opt','no-new-privileges',
            '--env','CUDA_VISIBLE_DEVICES=-1','--env','NVIDIA_VISIBLE_DEVICES=void',
            '--log-driver','none','--workdir','/workspace','--entrypoint','python',
            current_app.config.get('POLISH_VALIDATION_IMAGE','bookadaptor-polish-validation:latest'),'-c',RUNNER],
            capture_output=True,text=True,timeout=30)
        if result.returncode: raise RuntimeError('Validation container could not be created. Build the polish_validator image.')
        container=result.stdout.strip()
        copied=subprocess.run(['docker','cp','-',container+':/workspace'],stdin=archive,capture_output=True,timeout=60)
        if copied.returncode: raise RuntimeError('Could not prepare the isolated validation checkout.')
        result=subprocess.run(['docker','start','--attach',container],capture_output=True,text=True,timeout=330)
        marker='POLISH_VALIDATION_RESULT '
        lines=[line[len(marker):] for line in result.stdout.splitlines() if line.startswith(marker)]
        if result.returncode or not lines:
            raise RuntimeError('Isolated validation did not finish successfully; no change was applied. '+result.stderr[-1600:])
        return json.loads(lines[-1])
    except (OSError,subprocess.SubprocessError,RuntimeError) as error:
        raise ValidationFailure({'passed':False,'retryable':False,'stage':'infrastructure','message':str(error)}) from error
    finally:
        archive.close()
        if container:
            subprocess.run(['docker','rm','--force',container],capture_output=True,timeout=30)


def validate_change(path, before, after):
    try:
        syntax_check(path, after)
    except ValueError as error:
        raise ValidationFailure({'passed':False,'retryable':True,'stage':'syntax','message':str(error)}) from error
    if Path(path).suffix not in CODE_SUFFIXES:
        return {'passed':True,'message':'Text-only change; no executable code.'}
    root=Path(current_app.config['POLISH_WORKSPACE'])
    tests=relevant_tests(root,path)
    if not tests:
        raise ValidationFailure({'passed':False,'retryable':False,'stage':'coverage',
                                 'message':f'No relevant tests found for {path}; code was not applied.'})
    report=docker_validation(root,path,before,after,tests)
    if not report['passed']: raise ValidationFailure(report)
    return report
