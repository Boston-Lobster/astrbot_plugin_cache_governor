"""Build an immutable, reproducible runtime ZIP with SHA-256 manifests."""
import hashlib
import io
import json
import pathlib
import zipfile

from check_release import ROOT,RUNTIME_FILES,check,scan_text,version


def build():
    check()
    current=version()
    files={name:(ROOT/name).read_bytes() for name in sorted(RUNTIME_FILES)}
    files['PLUGIN_VERSION.json']=(json.dumps({'name':'astrbot_plugin_cache_governor','version':current,'tag':'v'+current},indent=2)+'\n').encode()
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,content in sorted(files.items()):
            scan_text(name,content)
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0))
            info.external_attr=0o100644<<16;info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,content)
    raw=output.getvalue()
    destination=ROOT/'dist';destination.mkdir(exist_ok=True)
    filename=f'astrbot_plugin_cache_governor-v{current}.zip'
    (destination/filename).write_bytes(raw)
    sha=lambda data:hashlib.sha256(data).hexdigest()
    manifest={'name':'astrbot_plugin_cache_governor','version':current,'tag':'v'+current,
        'archive':filename,'archive_sha256':sha(raw),'files':{name:sha(content) for name,content in sorted(files.items())}}
    manifest_raw=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
    (destination/'release-manifest.json').write_bytes(manifest_raw)
    (destination/'SHA256SUMS').write_text(f'{sha(raw)}  {filename}\n{sha(manifest_raw)}  release-manifest.json\n',encoding='utf-8',newline='\n')
    print(f'Built {filename}: {sha(raw)} ({len(raw)} bytes, {len(files)} files)')


if __name__=='__main__':build()
