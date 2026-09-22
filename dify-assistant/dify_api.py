"""Use the existing difyctl Windows credential; never print credential values."""
import sys, pathlib, json, ctypes
sys.path.insert(0, str(pathlib.Path(__file__).parent / '.deps'))
import requests
from ctypes import wintypes

class Credential(ctypes.Structure):
    _fields_ = [('Flags',wintypes.DWORD),('Type',wintypes.DWORD),('TargetName',wintypes.LPWSTR),('Comment',wintypes.LPWSTR),('LastWritten',wintypes.FILETIME),('CredentialBlobSize',wintypes.DWORD),('CredentialBlob',ctypes.POINTER(ctypes.c_ubyte)),('Persist',wintypes.DWORD),('AttributeCount',wintypes.DWORD),('Attributes',ctypes.c_void_p),('TargetAlias',wintypes.LPWSTR),('UserName',wintypes.LPWSTR)]

def credential():
    ptr=ctypes.POINTER(Credential)()
    dll=ctypes.WinDLL('Advapi32.dll')
    dll.CredReadW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(ctypes.POINTER(Credential))]
    if not dll.CredReadW('tokens.localhost.hhzdeo@gmail.com.difyctl',1,0,ctypes.byref(ptr)):
        raise RuntimeError('Existing difyctl credential unavailable')
    try:
        raw=ctypes.string_at(ptr.contents.CredentialBlob,ptr.contents.CredentialBlobSize)
        for encoding in ['utf-8','utf-16-le']:
            try: return json.loads(raw.decode(encoding))
            except (UnicodeError,ValueError): pass
        raise RuntimeError('Unsupported credential format')
    finally: dll.CredFree(ptr)

def session():
    data=credential()
    if isinstance(data,str):
        try: data=json.loads(data)
        except ValueError: data={'token':data}
    token=data.get('access_token') or data.get('accessToken') or data.get('token')
    if not token: raise RuntimeError('Credential keys: '+','.join(data.keys()))
    s=requests.Session()
    s.headers.update({'Authorization':'Bearer '+token})
    return s

def api(method,path,body=None):
    r=session().request(method,'http://localhost/openapi/v1'+path,json=body,timeout=180)
    if not r.ok: raise RuntimeError(f'{method} {path}: {r.status_code} {r.text[:1500]}')
    return r.json()

if __name__=='__main__':
    path=sys.argv[1]
    method=sys.argv[2] if len(sys.argv)>2 else 'GET'
    body=json.loads(pathlib.Path(sys.argv[3]).read_text('utf-8')) if len(sys.argv)>3 else None
    print(json.dumps(api(method,path,body),ensure_ascii=False))
