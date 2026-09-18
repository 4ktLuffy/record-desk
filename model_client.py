"""Small configurable JSON-chat client; credentials never enter reports."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


def complete(system, text, model=None):
    base=os.getenv('RECORD_DESK_BASE_URL','https://api.groq.com/openai/v1').rstrip('/')
    parsed=urllib.parse.urlparse(base)
    if parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','localhost','::1')):
        raise ValueError('Use HTTPS or a loopback URL for a local model server.')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Provider URL cannot contain credentials, query parameters, or fragments.')
    key=os.getenv('GROQ_API_KEY') if base=='https://api.groq.com/openai/v1' else os.getenv('RECORD_DESK_API_KEY')
    if not key and parsed.hostname not in ('127.0.0.1','localhost','::1'):
        raise ValueError('Configure a provider key in the server environment.')
    headers={'Content-Type':'application/json','User-Agent':'RecordDesk/0.2'}
    if key:headers['Authorization']='Bearer '+key
    payload=dict(model=model or os.getenv('EVAL_MODEL_NAME','openai/gpt-oss-20b'),temperature=0,max_tokens=3000,
                 response_format={'type':'json_object'},messages=[dict(role='system',content=system),dict(role='user',content=text)])
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    request=urllib.request.Request(base+'/chat/completions',data=json.dumps(payload).encode(),headers=headers)
    start=time.monotonic()
    try:
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=60) as response:
            data=json.load(response)
        choice=data['choices'][0]
        if choice.get('finish_reason')!='stop':
            raise ValueError('Provider did not complete the response.')
        result=json.loads(choice['message']['content'])
        if not isinstance(result,dict):raise ValueError('Expected a JSON object.')
        return dict(result=result,latency_seconds=round(time.monotonic()-start,3),usage=data.get('usage',{}))
    except urllib.error.HTTPError as exc:
        raise ValueError('Provider HTTP '+str(exc.code)) from None
    except (urllib.error.URLError,TimeoutError):
        raise ValueError('Provider connection failed or timed out.') from None
    except (KeyError,IndexError,TypeError,json.JSONDecodeError):
        raise ValueError('Provider returned an invalid JSON response.') from None
