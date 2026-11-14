"""Collect one bounded local repair bundle from exact installed vehicle assets.

This optional exporter does not install, modify, repack, or upload a mod. It
copies original stored PBO members; compressed members remain explicitly marked.
"""
from __future__ import annotations
from contextlib import contextmanager
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from itertools import chain
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import shutil
import zipfile
from tempfile import mkdtemp
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

from azimuth_app.checks import vehicle_wrap_evidence as evidence

COVERAGE = Path('Docs/Audit/Vehicle_Wrap_Installed_Config_Coverage.json')
CATALOG = Path('Custom/Transport/Workshop/catalog.json')
STATUSES = {'no_supported_exterior_selection', 'mapped_fitted_panels_only', 'mapped_requires_nonempty_opaque_readback'}
MAX_INPUT_JSON = 8 * 1024 * 1024
MAX_ZIP_BYTES = 500 * 1024 * 1024
MAX_PAYLOAD_BYTES = MAX_ZIP_BYTES - 8 * 1024 * 1024
MAX_RAW_LM_BYTES = 400 * 1024 * 1024
MAX_ASSET_BYTES = 32 * 1024 * 1024
MAX_SCRIPT_BYTES = 1024 * 1024
MAX_SCRIPT_TOTAL = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_TARGETS = 4096
MAX_FILES = 4096
MAX_SOURCE_SCRIPT_FILES = 1024
MAX_RESOURCE_VERSIONS = 4
MAX_LM_PROBE_BYTES = 4 * 1024 * 1024
MAX_LM_TAIL_BYTES = 64 * 1024
MAX_SOURCE_PBOS = 256
MAX_SOURCE_HEADER_BYTES = 8 * 1024 * 1024
MAX_SOURCE_ENTRIES = 20000
CHUNK = 1024 * 1024
RESOURCE_SUFFIXES = {'.p3d', '.paa', '.pac', '.rvmat'}
SOURCE_ID = re.compile(r'workshop_([0-9]{6,20})_library_[0-9]+\Z')


def _resource(value: str) -> str:
    value = evidence._relative_name(value.replace('\\','/').lstrip('/'))
    if len(value) > 512 or PurePosixPath(value).suffix.casefold() not in RESOURCE_SUFFIXES:
        raise ValueError('Unsupported repair resource path.')
    return value.casefold()


def _json(root: Path, relative: Path) -> dict:
    path = root
    for index, part in enumerate(relative.parts):
        path = path / part
        if not evidence._ordinary(path, index < len(relative.parts) - 1):
            raise ValueError('Repair request data must use ordinary bundled files.')
    with path.open('rb') as stream:
        data = stream.read(MAX_INPUT_JSON + 1)
    if len(data) > MAX_INPUT_JSON:
        raise ValueError('Repair request data exceeds its size limit.')
    result = json.loads(data.decode('utf-8-sig'))
    if not isinstance(result, dict):
        raise ValueError('Repair request data must be an object.')
    return result


def _targets(root: Path) -> tuple[dict[str, list[str]], set[str]]:
    coverage = _json(root,COVERAGE)
    catalog = _json(root,CATALOG)
    if coverage.get('schema_version') != 1 or catalog.get('schema_version') != 1:
        raise ValueError('Unsupported repair request data version.')
    targets: dict[str,list[str]] = {}
    owners = {'3639695989'}
    publisher_assembled = {v.get('class') for p in catalog.get('packs', []) for v in p.get('vehicles', [])
                           if p.get('id') in evidence.WORKSHOP_IDS and v.get('assembly') == 'publisher_on_spawn'}
    def add(path, reason):
        if not isinstance(path,str) or not path or path.startswith('#'):
            return
        if PurePosixPath(path.replace('\\','/')).suffix.casefold() not in RESOURCE_SUFFIXES:
            return
        key = _resource(path)
        if key not in targets:
            if len(targets) >= MAX_TARGETS: raise ValueError('Too many requested repair resources.')
            targets[key] = []
        if reason not in targets[key] and len(targets[key]) < 32: targets[key].append(reason)
    def row_assets(row, reason):
        add(row.get('model'),reason)
        for surface in row.get('declared_surfaces',[]):
            add(surface.get('configured_texture'),reason)
            add(surface.get('configured_material'),reason)
        for origin in row.get('origins',[]):
            match = SOURCE_ID.fullmatch(str(origin.get('source','')))
            if match and match[1] in evidence.WORKSHOP_IDS: owners.add(match[1])
    vehicles = coverage.get('vehicle_classes',[])
    panels = coverage.get('panel_classes',[])
    if not isinstance(vehicles,list) or not isinstance(panels,list) or len(vehicles)+len(panels)>10000:
        raise ValueError('Invalid vehicle repair coverage rows.')
    for row in vehicles:
        if not isinstance(row,dict): raise ValueError('Invalid vehicle repair row.')
        if row.get('scope') == 2 and (row.get('status') in STATUSES or row.get('class') in publisher_assembled
                                     or 'zodiac' in str(row.get('model','')).lower()):
            row_assets(row, 'vehicle:' + str(row.get('class','')))
    for row in panels:
        if not isinstance(row,dict): raise ValueError('Invalid vehicle repair panel row.')
        if row.get('scope') == 2 and not row.get('mapped_selections'):
            row_assets(row, 'unmapped_panel:' + str(row.get('class','')))
    for pack in catalog.get('packs',[]):
        if pack.get('id') not in evidence.WORKSHOP_IDS: continue
        issues = pack.get('asset_issues',[])
        if issues: owners.add(pack['id'])
        for issue in issues:
            add(issue.get('resource') or issue.get('model'),'missing_publisher_asset:' + pack['id'])
    return targets, owners


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _check_file(path: Path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info,'st_file_attributes',0) & 0x400:
        raise ValueError('Repair source is not an ordinary file.')
    return info


@contextmanager
def _open_unchanged(path: Path, expected=None):
    before = _check_file(path)
    if expected is not None and _identity(before) != expected:
        raise ValueError('Repair source changed before collection.')
    flags = os.O_RDONLY | getattr(os,'O_BINARY',0) | getattr(os,'O_NOFOLLOW',0)
    descriptor = os.open(path,flags)
    with os.fdopen(descriptor,'rb') as stream:
        opened = os.fstat(stream.fileno())
        if _identity(opened) != _identity(before) or _identity(_check_file(path)) != _identity(before):
            raise ValueError('Repair source changed while opening.')
        yield stream, before
        if _identity(os.fstat(stream.fileno())) != _identity(before) or _identity(_check_file(path)) != _identity(before):
            raise ValueError('Repair source changed during collection.')


@dataclass(frozen=True)
class Payload:
    path: Path
    identity: tuple
    offset: int
    size: int
    original_size: int
    method: int
    destination: str
    kind: str
    resource: str
    source: str
    archive: str


class _HeaderStream:
    def __init__(self, stream):
        self.stream = stream
        self.read_bytes = 0

    def read(self, size):
        data = self.stream.read(size)
        self.read_bytes += len(data)
        return data

    def seek(self, offset):
        return self.stream.seek(offset)


def _prefix(stream, limit=None):
    """Read only the small PBO property block; never enumerate unrelated sounds."""
    reader = evidence.HeaderReader(stream, limit)
    name = reader.string()
    method, original, reserved, timestamp, stored = evidence.ENTRY.unpack(reader.take(evidence.ENTRY.size))
    if name or method != evidence.VERS:
        return None, reader.count
    prefix = None
    for _ in range(256):
        key = reader.string()
        if not key:
            return prefix, reader.count
        value = reader.string()
        if key.casefold() == 'prefix' and value:
            prefix = evidence._relative_name(value.strip('\\/')).casefold()
    raise ValueError('Too many PBO properties')


def _scan(roots, targets, owners):
    plan = []
    records = []
    notes = []
    sources = []
    global_scanned = global_headers = global_entries = 0
    # Every source gets its own bounded allowance. A native sound archive or a
    # corrupt publisher cannot consume another publisher's scan allowance.
    for label,folder,native in sorted(roots, key=lambda r: (r[2],not r[0].startswith('workshop_3639695989_'),r[0])):
        match = SOURCE_ID.fullmatch(label)
        wid = match[1] if match else None
        if not native and wid not in owners: continue
        source = {'source': label, 'native': native, 'status': 'complete', 'archives_checked': 0,
                  'members_indexed': 0, 'header_bytes_read': 0, 'scripts_not_planned': 0}
        sources.append(source)
        scanned = headers = entries = script_plans = 0
        versions = defaultdict(int)
        try:
            # The known opaque LM container is gathered before parsing any
            # auxiliary addon from that publisher.
            exact_lm = evidence._case_child(folder,'LM_Planes.pbo') if wid=='3639695989' else None
            priority = [exact_lm] if exact_lm is not None and evidence._ordinary(exact_lm) else []
            seen_paths = set()
            for path in chain(priority,evidence._walk_pbos(folder,False)):
                if path in seen_paths: continue
                seen_paths.add(path)
                if global_scanned >= evidence.MAX_PBOS or global_headers >= evidence.MAX_TOTAL_HEADER_BYTES or global_entries >= evidence.MAX_TOTAL_ENTRIES:
                    source['status'] = 'global_scan_limit'
                    notes.append(f'{label}: remaining archives were not scanned because the aggregate scan limit was reached.')
                    break
                if scanned >= MAX_SOURCE_PBOS or headers >= MAX_SOURCE_HEADER_BYTES or entries >= MAX_SOURCE_ENTRIES:
                    source['status'] = 'scan_limit'
                    notes.append(f'{label}: remaining archives were not scanned because this source reached its archive/header/member limit.')
                    break
                scanned += 1
                global_scanned += 1
                relative = evidence._relative_name(path.relative_to(folder).as_posix())
                record = {'source':label,'archive':relative}
                records.append(record)
                header_stream = None
                accounted = 0
                try:
                    with _open_unchanged(path) as (stream,before):
                        identity = _identity(before)
                        header_stream = _HeaderStream(stream)
                        if wid == '3639695989' and path.name.casefold() == 'lm_planes.pbo':
                            if before.st_size > MAX_RAW_LM_BYTES:
                                record.update(status='raw_lm_over_400_mib_limit',bytes=before.st_size)
                                amount = min(before.st_size, MAX_LM_PROBE_BYTES)
                                plan.append(Payload(path,identity,0,amount,amount,0,
                                                    f'raw_lm/{label}/LM_Planes.header.bin','raw_lm_probe','',label,relative))
                                tail = min(before.st_size-amount, MAX_LM_TAIL_BYTES)
                                if tail:
                                    plan.append(Payload(path,identity,before.st_size-tail,tail,tail,0,
                                                        f'raw_lm/{label}/LM_Planes.tail.bin','raw_lm_probe','',label,relative))
                            else:
                                record.update(status='raw_lm_requested',bytes=before.st_size)
                                plan.append(Payload(path,identity,0,before.st_size,before.st_size,0,
                                                    f'raw_lm/{label}/LM_Planes.pbo','raw_lm','',label,relative))
                            continue
                        if native:
                            prefix, peek_bytes = _prefix(header_stream,min(MAX_SOURCE_HEADER_BYTES-headers,
                                                                         evidence.MAX_TOTAL_HEADER_BYTES-global_headers))
                            headers += peek_bytes
                            global_headers += peek_bytes
                            accounted += peek_bytes
                            if prefix and not any(resource.startswith(prefix + '/') for resource in targets):
                                record.update(status='unrelated_native_prefix',virtual_prefix=prefix)
                                continue
                            header_stream.seek(0)
                        header_room = min(MAX_SOURCE_HEADER_BYTES-headers,evidence.MAX_TOTAL_HEADER_BYTES-global_headers)
                        if header_room <= 0: raise ValueError('PBO header exceeds its remaining scan budget')
                        members,prefix,used,header_hash = evidence._directory(header_stream,before.st_size,header_room)
                        headers += used
                        global_headers += used
                        accounted += used
                        entries += len(members)
                        global_entries += len(members)
                        if entries > MAX_SOURCE_ENTRIES:
                            record['status']='over_source_member_limit';source['status']='scan_limit';continue
                        if global_entries > evidence.MAX_TOTAL_ENTRIES:
                            record['status']='over_aggregate_member_limit';source['status']='global_scan_limit';continue
                        record.update(status='indexed',header_sha256=header_hash,virtual_prefix=prefix)
                        chosen = []
                        for member in members:
                            if len(member.name)>512:continue
                            full = (prefix.rstrip('/')+'/'+member.name).strip('/').replace('\\','/').casefold()
                            suffix = PurePosixPath(member.name).suffix.casefold()
                            kind = None
                            if full in targets: kind = 'model' if suffix=='.p3d' else 'texture' if suffix in {'.paa','.pac'} else 'material'
                            elif not native and wid in owners and suffix=='.c':
                                if script_plans >= MAX_SOURCE_SCRIPT_FILES:
                                    source['scripts_not_planned'] += 1
                                    continue
                                script_plans += 1
                                kind='script'
                            if kind:chosen.append((member,full,kind))
                        if chosen:
                            chosen += [(m,'','config') for m in members if evidence._is_config(m)]
                        for member,full,kind in chosen:
                            key = full if full else 'config:' + relative + '/' + member.name
                            if versions[key] >= MAX_RESOURCE_VERSIONS:
                                record['resource_version_limit'] = True
                                source['status'] = 'payload_plan_limit'
                                continue
                            versions[key] += 1
                            suffix = '' if member.method==0 else '.stored'
                            destination=f'members/{label}/{scanned:04d}/{member.name}{suffix}'
                            plan.append(Payload(path,identity,member.offset,member.stored_size,member.original_size,
                                                member.method,destination,kind,full,label,relative))
                except (OSError,ValueError,UnicodeError,struct.error) as error:
                    record.update(status='could_not_inspect',error=type(error).__name__)
                    source['status'] = 'inspection_error'
                    charged = max(0,header_stream.read_bytes-accounted) if header_stream else 0
                    headers += charged
                    global_headers += charged
        except (OSError,ValueError):
            source['status'] = 'enumeration_error'
            notes.append(f'Some files in {label} could not be enumerated.')
        source.update(archives_checked=scanned,members_indexed=entries,header_bytes_read=headers)
        if source['scripts_not_planned']:
            notes.append(f"{label}: {source['scripts_not_planned']} script entries exceeded this source's planning allowance.")
    return plan,records,notes,sources


def _ordered(plan):
    """Exact assets first; round-robin sources so one mod cannot monopolize scripts."""
    for kind in ('model','texture','material','config','script','raw_lm_probe','raw_lm'):
        grouped = defaultdict(deque)
        for item in sorted((p for p in plan if p.kind == kind),key=lambda p:(p.source,p.destination)):
            grouped[item.source].append(item)
        queues = deque(grouped.values())
        while queues:
            queue = queues.popleft()
            yield queue.popleft()
            if queue: queues.append(queue)


def _copy_payload(archive: ZipFile, item: Payload) -> str:
    digest=sha256()
    with _open_unchanged(item.path,item.identity) as (stream,_):
        stream.seek(item.offset)
        left=item.size
        with archive.open(item.destination,'w') as output:
            while left:
                chunk=stream.read(min(CHUNK,left))
                if not chunk: raise ValueError('Repair source truncated during collection.')
                output.write(chunk);digest.update(chunk);left-=len(chunk)
    return digest.hexdigest()


def _payload_hash(item: Payload) -> str:
    digest = sha256()
    with _open_unchanged(item.path,item.identity) as (stream,_):
        stream.seek(item.offset)
        left = item.size
        while left:
            block = stream.read(min(CHUNK,left))
            if not block: raise ValueError('Repair source truncated during collection.')
            digest.update(block);left -= len(block)
    return digest.hexdigest()


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(CHUNK),b''): digest.update(block)
    return digest.hexdigest()


def read_collection_summary(path: Path) -> dict:
    """Read bounded, validated collection status for the control panel."""
    try:
        _check_file(Path(path))
        with ZipFile(path) as archive:
            info = archive.getinfo('Vehicle_Repair_Files.json')
            if info.file_size > MAX_MANIFEST_BYTES: raise ValueError('Repair manifest is too large.')
            report = json.loads(archive.read(info))
        if not isinstance(report,dict) or report.get('schema_version') != 1:
            raise ValueError('Invalid repair manifest.')
        counts = report.get('counts')
        if not isinstance(counts,dict): raise ValueError('Invalid repair counts.')
        result = {}
        for key in ('requested_assets','missing_assets','copied_assets','limited_files','source_count'):
            value = counts.get(key)
            if type(value) is not int or not 0 <= value <= 1000000: raise ValueError('Invalid repair counts.')
            result[key] = value
        if result['copied_assets'] + result['missing_assets'] != result['requested_assets']:
            raise ValueError('Repair asset counts disagree.')
        partial = report.get('partial_scan')
        if type(partial) is not bool: raise ValueError('Invalid repair scan state.')
        result['partial_scan'] = partial
        lm = report.get('raw_lm')
        if not isinstance(lm,dict) or lm.get('status') not in {
                'copied','copied_in_companion','not_found','source_not_located','not_scanned',
                'not_collected','over_size_limit'}:
            raise ValueError('Invalid LM collection state.')
        result['lm_status'] = lm['status']
        companions = report.get('companion_files',[])
        if not isinstance(companions,list) or len(companions)>1: raise ValueError('Invalid companion list.')
        result['companion_files'] = []
        for row in companions:
            name = row.get('file_name') if isinstance(row,dict) else None
            if not isinstance(name,str) or PurePosixPath(name).name != name or not name.endswith('.zip'):
                raise ValueError('Invalid companion filename.')
            name = evidence._relative_name(name)
            companion = Path(path).parent/name
            size = row.get('bytes')
            if type(size) is not int or not 0 < size <= MAX_ZIP_BYTES or _check_file(companion).st_size != size:
                raise ValueError('Repair companion is missing or changed.')
            result['companion_files'].append(name)
        return result
    except (OSError,KeyError,TypeError,UnicodeError,json.JSONDecodeError,zipfile.BadZipFile,zipfile.LargeZipFile) as error:
        raise ValueError('Could not read the completed repair collection.') from error


def collect_vehicle_repair_assets(root: Path, libraries: list[Path], installation: Path | None = None) -> Path:
    """Return the main ZIP in an atomic collection folder; each ZIP <=500 MiB.

    An opaque LM archive gets its own companion when needed, so it never crowds
    out the requested models. No source, server, save or setting is changed.
    """
    root=Path(root).resolve(strict=True)
    targets,owners=_targets(root)
    roots,_,discovery_notes=evidence._discover([Path(p) for p in libraries],installation)
    plan,records,notes,sources=_scan(roots,targets,owners)
    folder=evidence._output_directory(root)
    created=datetime.now(timezone.utc)
    token=f'{created:%Y%m%d_%H%M%S}_UTC_{uuid4().hex[:12]}'
    collection=folder/f'Azimuth_Vehicle_Repair_{token}'
    filename=f'Azimuth_Vehicle_Repair_Files_{token}.zip'
    companion_name=f'Azimuth_LM_Planes_Repair_Files_{token}.zip'
    present_owners={match[1] for label,_,native in roots
                    if not native and (match:=SOURCE_ID.fullmatch(label))}
    partial_scan=any(s['status']!='complete' or s['scripts_not_planned'] for s in sources)
    manifest={'schema_version':1,'created_utc':created.isoformat(),
              'purpose':'Exact original installed assets needed for unresolved vehicle repair analysis',
              'actions':'Read-only local collection; no installation, repacking, uploading, or server changes',
              'limits':{'zip_bytes':MAX_ZIP_BYTES,'raw_lm_bytes':MAX_RAW_LM_BYTES,'script_bytes_total':MAX_SCRIPT_TOTAL,
                        'source_pbos':MAX_SOURCE_PBOS,'source_header_bytes':MAX_SOURCE_HEADER_BYTES,
                        'source_members':MAX_SOURCE_ENTRIES,'aggregate_pbos':evidence.MAX_PBOS,
                        'aggregate_header_bytes':evidence.MAX_TOTAL_HEADER_BYTES,'aggregate_members':evidence.MAX_TOTAL_ENTRIES},
              'excluded':'No saves, profiles, logs, keys, credentials, or arbitrary folder copies',
              'encoding':'Stored compressed/unknown PBO members keep exact bytes with .stored suffix; they are not decoded resources.',
              'native_validation':'Not run','archives':records,'sources':sources,
              'missing_workshop_sources':sorted(owners-present_owners),
              'partial_scan':partial_scan,'notes':discovery_notes+notes,'files':[],'companion_files':[]}
    staging=Path(mkdtemp(prefix='.vehicle-repair-',dir=folder))
    target=staging/filename
    companion=None
    total=main_total=raw_total=scripts=count=deduplicated=0
    copied=set()
    hashes=defaultdict(dict)
    try:
        with ZipFile(target,'w',ZIP_DEFLATED,compresslevel=1) as archive:
            for item in _ordered(plan):
                row={'source':item.source,'archive':item.archive,'resource':item.resource,'kind':item.kind,
                     'stored_bytes':item.size,'original_bytes':item.original_size,'method_hex':f'{item.method:08x}'}
                manifest['files'].append(row)
                limit=MAX_RAW_LM_BYTES if item.kind=='raw_lm' else MAX_SCRIPT_BYTES if item.kind=='script' else evidence.MAX_CONFIG_BYTES if item.kind=='config' else MAX_ASSET_BYTES
                if max(item.size,item.original_size)>limit:row['status']='over_entry_limit';continue
                key=(item.resource or ('LM_Planes.pbo' if item.kind=='raw_lm' else ''),item.kind,item.method,item.original_size,item.size)
                if key[0] and key in hashes:
                    digest=_payload_hash(item)
                    existing=hashes[key].get(digest)
                    if existing:
                        row.update(status='copied',export_path=existing['export_path'],sha256=digest,
                                   encoding=existing['encoding'],shared_payload=True)
                        if 'companion_file' in existing:row['companion_file']=existing['companion_file']
                        deduplicated+=1
                        if item.resource in targets:copied.add(item.resource)
                        continue
                if count>=MAX_FILES:row['status']='over_file_limit';continue
                if item.kind=='script' and item.size>MAX_SCRIPT_TOTAL-scripts:row['status']='over_script_total_limit';continue
                if item.kind=='raw_lm' and item.size>MAX_RAW_LM_BYTES-raw_total:
                    row['status']='over_raw_lm_total_limit';continue
                if item.size>MAX_PAYLOAD_BYTES-main_total:
                    if item.kind!='raw_lm':row['status']='over_total_limit';continue
                    if companion is None:companion=ZipFile(staging/companion_name,'w',ZIP_DEFLATED,compresslevel=1)
                    destination=companion
                    row['companion_file']=companion_name
                else:
                    destination=archive
                    main_total+=item.size
                digest=_copy_payload(destination,item)
                row.update(status='copied',export_path=item.destination,sha256=digest,
                           encoding='raw' if item.method==0 else 'stored_compressed_or_unknown')
                total+=item.size;count+=1
                if item.kind=='raw_lm':raw_total+=item.size
                if item.kind=='script':scripts+=item.size
                if item.resource in targets:copied.add(item.resource)
                if key[0]:hashes[key][digest]=dict(row)
            if companion is not None:
                companion.writestr('Vehicle_Repair_Companion.json',json.dumps({
                    'schema_version':1,'main_file':filename,'created_utc':created.isoformat(),
                    'purpose':'Original LM archive; collected alongside the main repair ZIP.',
                    'files':[row for row in manifest['files'] if row.get('companion_file')==companion_name]},indent=2)+'\n')
                companion.close();companion=None
                companion_path=staging/companion_name
                if companion_path.stat().st_size>MAX_ZIP_BYTES:raise ValueError('Repair companion ZIP exceeds the 500 MiB limit.')
                manifest['companion_files'].append({'file_name':companion_name,'bytes':companion_path.stat().st_size,
                                                    'sha256':_file_hash(companion_path)})
            found={row['resource']:row.get('status') for row in manifest['files'] if row['resource'] in targets}
            manifest['requested_assets']=[{'resource':p,'reasons':reasons,
                                          'status':'copied' if p in copied else found.get(p) or
                                                   ('not_collected_scan_incomplete' if partial_scan else 'not_found_in_scanned_sources')}
                                         for p,reasons in sorted(targets.items())]
            manifest['missing_assets']=[p for p in sorted(targets) if p not in copied]
            lm_files=[row for row in manifest['files'] if row['kind']=='raw_lm']
            lm_archives=[row for row in records if row['source'].startswith('workshop_3639695989_') and PurePosixPath(row['archive']).name.casefold()=='lm_planes.pbo']
            lm_sources=[s for s in sources if s['source'].startswith('workshop_3639695989_')]
            if any(row.get('status')=='copied' and row.get('companion_file') for row in lm_files):lm_status='copied_in_companion'
            elif any(row.get('status')=='copied' for row in lm_files):lm_status='copied'
            elif any(row.get('status')=='raw_lm_over_400_mib_limit' for row in lm_archives):lm_status='over_size_limit'
            elif lm_archives:lm_status='not_collected'
            elif not lm_sources:lm_status='source_not_located'
            elif any(s['status']!='complete' for s in lm_sources):lm_status='not_scanned'
            else:lm_status='not_found'
            diagnostic=any(row['kind']=='raw_lm_probe' and row.get('status')=='copied' for row in manifest['files'])
            manifest['raw_lm']={'archive':'LM_Planes.pbo','status':lm_status,'maximum_bytes':MAX_RAW_LM_BYTES,
                                'archives':lm_archives,'diagnostic_only':lm_status=='over_size_limit' and diagnostic,
                                'diagnostics_collected':diagnostic}
            manifest['counts']={'copied_files':count,'copied_stored_bytes':total,'main_stored_bytes':main_total,
                                'copied_script_bytes':scripts,'requested_assets':len(targets),'copied_assets':len(copied),
                                'missing_assets':len(targets)-len(copied),'source_count':len(sources),'deduplicated_sources':deduplicated,
                                'limited_files':sum(row.get('status')!='copied' for row in manifest['files'])}
            content=(json.dumps(manifest,indent=2,ensure_ascii=True)+'\n').encode('utf-8')
            if len(content)>MAX_MANIFEST_BYTES:raise ValueError('Repair manifest exceeds its size limit.')
            archive.writestr('Vehicle_Repair_Files.json',content)
        if target.stat().st_size>MAX_ZIP_BYTES:raise ValueError('Repair ZIP exceeds the 500 MiB limit.')
        read_collection_summary(target)
        if collection.exists():raise FileExistsError('Repair output already exists.')
        os.replace(staging,collection);staging=None
        return collection/filename
    finally:
        try:
            if companion is not None:companion.close()
        finally:
            if staging is not None:shutil.rmtree(staging)
