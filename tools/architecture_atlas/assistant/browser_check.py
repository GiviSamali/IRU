"""Explicit local browser protocol fixture. Never calls DeepSeek. Not a production mode."""
import argparse
import tempfile
from pathlib import Path
from core import Snapshot
from server import Agent, Handler, ROOT, ThreadingHTTPServer
from test_assistant import call


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8775);args=parser.parse_args()
    count=0
    def provider(messages):
        nonlocal count
        count+=1
        if count==1:return call('search_nodes',{'query':'Goal','limit':3})
        if count==2:return call('read_source',{'path':'server/worker_scheduler.py','start':189,'count':5})
        if count==3:return call('select_node',{'node_id':'component:goals','focus':True})
        if count==4:return call('zoom',{'factor':.8})
        if count==5:return call('show_path',{'source':'component:orchestrator','target':'component:worker','types':['depends_on']})
        if count==6:return call('remember_note',{'text':'[ТЕСТ ПРОТОКОЛА] Никакой запрос к DeepSeek не выполнялся.'})
        if any(m.get('role')=='tool' and '"ok":false' in m.get('content','') for m in messages):raise ValueError('Local browser receipt check failed')
        return {'role':'assistant','content':'[ТЕСТ ПРОТОКОЛА БЕЗ LLM] Исходник прочитан, Goal выбран, масштаб изменён, маршрут Orchestrator → Worker показан; браузер подтвердил команды.'}
    private=ROOT/'.local'/'checks';private.mkdir(parents=True,exist_ok=True)
    assert private.resolve().is_relative_to(ROOT.resolve())
    with tempfile.TemporaryDirectory(prefix='browser-check-',dir=private) as folder:
        agent=Agent(Snapshot(ROOT),Path(folder),provider=provider)
        server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler);server.agent=agent
        print('LOCAL PROTOCOL TEST ONLY http://127.0.0.1:'+str(args.port),flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:server.server_close()


if __name__=='__main__':main()
