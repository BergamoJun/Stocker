"""Explicit-window metadata calls sharing the existing request budgets."""
from urllib.parse import urlencode
from urllib.request import Request
from research import news_transport
from research.rights import as_utc
from research.panel_transport import SourceUnavailable
from .queued_news import Client as QueuedClient

class Client(QueuedClient):
    def gdelt_window(self,query,start,end):
        self.check()
        if not isinstance(query,str) or not 1<=len(query)<=512 or not as_utc(start)<as_utc(end)<=as_utc(self.clock()): raise ValueError('INVALID_ON_DEMAND_WINDOW')
        if self.gdelt_requests>=4: raise SourceUnavailable('RUN_REQUEST_CAP')
        parameters={'query':query,'mode':'artlist','format':'json','maxrecords':25,'sort':'datedesc',
            'startdatetime':as_utc(start).strftime('%Y%m%d%H%M%S'),'enddatetime':as_utc(end).strftime('%Y%m%d%H%M%S')}
        request=Request('https://api.gdeltproject.org/api/v2/doc/doc?'+urlencode(parameters),headers={'User-Agent':'Stocker-personal-on-demand-metadata/0.1'})
        owner=self
        class Counted:
            def open(self,req,timeout):
                owner.check(); owner.gdelt_requests+=1
                return owner.opener.open(req,timeout=timeout)
        return news_transport.request_metadata(request,Counted(),clock=self.clock,sleep=self.sleep,state_dir=self.gdelt_root)
