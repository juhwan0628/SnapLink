import http.client, importlib.util, json, tempfile, threading
from pathlib import Path
from http.server import ThreadingHTTPServer
spec=importlib.util.spec_from_file_location('store',str(Path(__file__).with_name('slides_asset_server.py')))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with tempfile.TemporaryDirectory() as d:
 m.ROOT=Path(d);(m.ROOT/'media').mkdir();(m.ROOT/'upload.key').write_text('test-key')
 server=ThreadingHTTPServer(('127.0.0.1',0),m.Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
 def request(method,path,body=None,token=None):
  c=http.client.HTTPConnection(*server.server_address);c.request(method,path,body,{'Authorization':'Bearer '+token} if token else {});r=c.getresponse();result=(r.status,json.loads(r.read()));c.close();return result
 assert request('POST','/slides/api/assets/service-icon',b'x')[0]==401
 assert request('POST','/slides/api/assets/../../index.html',b'x','test-key')[0]==404
 assert request('POST','/slides/api/assets/service-icon',b'<svg/>','test-key')[0]==400
 import io
 buffer=io.BytesIO();m.Image.new('RGBA',(2,2),(40,91,197,255)).save(buffer,format='PNG');data=buffer.getvalue()
 status,saved=request('POST','/slides/api/assets/service-icon',data,'test-key');assert status==200,(status,saved)
 assert request('GET','/slides/api/assets')[1]['service-icon']==saved['url']
 assert (m.ROOT/'media'/saved['url'].split('/')[-1]).read_bytes()==data
 server.shutdown();server.server_close();print('Auth, invalid paths/files, upload, persistence: passed')
