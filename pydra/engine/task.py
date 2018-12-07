# -*- coding: utf-8 -*-
"""task.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1RRV1gHbGJs49qQB1q1d5tQEycVRtuhw6

## Notes:

### Environment specs
1. neurodocker json
2. singularity file+hash
3. docker hash
4. conda env
5. niceman config
6. environment variables

### Monitors/Audit
1. internal monitor
2. external monitor
3. callbacks

### Resuming
1. internal tracking
2. external tracking (DMTCP)

### Provenance
1. Local fragments
2. Remote server

### Isolation
1. Working directory
2. File (copy to local on write)
3. read only file system
"""


import abc
import dataclasses as dc
import datetime as dt
from hashlib import sha256
import json
import os
import pickle as cp
from tempfile import mkdtemp
import typing as ty
import inspect
import asyncio
from pathlib import Path

File = ty.NewType('File', Path)
Directory = ty.NewType('Directory', Path)

def ensure_list(obj):
    if obj is None:
        return []
    if isinstance(obj, list):
        return obj
    return [obj]

def print_help(obj):
    helpstr = 'help {}'.format(obj)
    print(helpstr)
    return helpstr

def load_result(checksum, cache_locations):
    if not cache_locations:
        return None
      
    for location in cache_locations:
        if (location / checksum).exists():
            return cp.loads(
                (location / checksum / '_result.pklz').read_bytes())
    return None

def save_result(result_path: Path, result):
    with (result_path / '_result.pklz').open('wb') as fp:
        return cp.dump(dc.asdict(result), fp)

def task_hash(task_obj):
    """
    input hash, output hash, environment hash
    
    :param task_obj: 
    :return: 
    """
    return NotImplementedError

def now():
    return dt.datetime.utcnow().isoformat(timespec='microseconds')

prov_context = "https://openprovenance.org/prov.jsonld"
schema_context = "https://schema.org/docs/jsonldcontext.json"
pydra_context = {"pydra": "https://uuid.pydra.org/"}

# audit flags
AUDIT_PROV = 0x01
AUDIT_RESOURCE = 0x02


def gen_uuid():
    import uuid
    return uuid.uuid4().hex

class Messenger:
    @abc.abstractmethod
    def send(self, message, **kwargs):
        pass

class PrintMessenger(Messenger):
    
    def send(self, message, **kwargs):
        import json
        mid = gen_uuid()
        print('id: {0}\n{1}'.format(mid,
                                    json.dumps(message, ensure_ascii=False, 
                                               indent=2, sort_keys=False)))

class FileMessenger(Messenger):
    
    def send(self, message, **kwargs):
        import json
        mid = gen_uuid()
        with open(os.path.join(kwargs['message_dir'], 
                               mid + '.jsonld'), 'wt') as fp:
            json.dump(message, fp, ensure_ascii=False, indent=2, 
                      sort_keys=False)

class RemoteRESTMessenger(Messenger):

    def send(self, message, **kwargs):
        import requests
        r = requests.post(kwargs['post_url'], json=message, 
                          auth=kwargs['auth']() if getattr(kwargs['auth'], 
                                                           '__call__', None) 
                                                else kwargs['auth'])
        return r.status_code

def send_message(message, messengers=None, **kwargs):
    """Send nidm messages for logging provenance and auditing
    """
    for messenger in messengers:
        messenger.send(message, **kwargs)

def make_message(obj, context="https://schema.pydra.org/context.jsonld"):
    message = {"@context": context}
    message.update(**obj)
    return message

@dc.dataclass
class RuntimeSpec:
    outdir: ty.Optional[str] = None
    container: ty.Optional[str] = 'shell'
    network: bool = False
    """
    from CWL:
    InlineJavascriptRequirement
    SchemaDefRequirement
    DockerRequirement
    SoftwareRequirement
    InitialWorkDirRequirement
    EnvVarRequirement
    ShellCommandRequirement
    ResourceRequirement
    
    InlineScriptRequirement
    """

@dc.dataclass
class BaseSpec:
    @property
    def hash(self):
        return sha256(str(self).encode()).hexdigest()

@dc.dataclass
class Result:
    output: ty.Optional[ty.Any] = None

class BaseTask:
    """This is a base class for Task objects.
    """

    _api_version: str = "0.0.1"  # Should generally not be touched by subclasses
    _task_version: ty.Optional[str] = None  # Task writers encouraged to define and increment when implementation changes sufficiently
    _version: str  # Version of tool being wrapped

    input_spec = BaseSpec  # See BaseSpec
    output_spec = BaseSpec  # See BaseSpec
    audit_flags: ty.Optional[bool] = None  # What to audit. See audit flags for details

    _can_resume = False  # Does the task allow resuming from previous state
    _redirect_x = False  # Whether an X session should be created/directed

    _runtime_requirements = RuntimeSpec()
    _runtime_hints = None

    _input_sets = None  # Dictionaries of predefined input settings
    _cache_dir = None  # Working directory in which to operate
    _references = None  # List of references for a task
    

    def __init__(self, inputs: ty.Optional[ty.Text]=None,
                 audit_flags: bool=False,
                 messengers=None, messenger_args=None):
        """Initialize task with given args."""
        super().__init__()
        if not self.input_spec:
            raise Exception(
                'No input_spec in class: %s' % self.__class__.__name__)
        self.inputs = self.input_spec(
            **{f.name:None
               for f in dc.fields(self.input_spec)
               if f.default is dc.MISSING})
        self.audit_flags = audit_flags
        self.messengers = ensure_list(messengers)
        self.messenger_args = messenger_args
        if inputs:
            if isinstance(inputs, dict):
                self.inputs = dc.replace(self.inputs, **inputs)
            elif Path(inputs).is_file():
                inputs = json.loads(Path(inputs).read_text())
            elif isinstance(defaults, str):
                inputs = self._input_sets[inputs]

    def audit(self, message, flags=None):
        if self.audit_flags and flags:
            if self.messenger_args:
                send_message(make_message(message), messengers=self.messengers, 
                             **self.messenger_args)
            else:              
                send_message(make_message(message), messengers=self.messengers)

    @property
    def can_resume(self):
        """Task can reuse partial results after interruption
        """
        return self._can_resume

    @classmethod
    def help(cls, returnhelp=False):
        """ Prints class help
        """
        helpstr = print_help(cls)
        if returnhelp:
            return helpstr

    @property
    def output_names(self):
        return [f.name for f in dc.fields(self.output_spec)]

    @property
    def version(self):
        return self._version
    
    def save_set(self, name, inputs, force=False):
        if name in self._input_sets and not force:
            raise KeyError('Key {} already saved. Use force=True to override.')
        self._input_sets[name] = inputs

    @property
    def checksum(self):
        return self.inputs.hash

    @abc.abstractmethod
    async def _run_interface(self, **kwargs):
        pass

    def result(self, cache_locations=None):
        result = load_result(self.checksum, 
                             ensure_list(cache_locations) + 
                             ensure_list(self._cache_dir))
        if result is not None:
            if 'output' in result:
                output = self.output_spec(**result['output'])
            return Result(output=output)
        return None

    @property
    def cache_dir(self):
        return self._cache_dir
    
    @cache_dir.setter
    def cache_dir(self, location):
        self._cache_dir = Path(location)

    def run(self, cache_locations=None, **kwargs):
        self.inputs = dc.replace(self.inputs, **kwargs)
        inputs_hash = self.inputs.hash
        
        # Eagerly retrieve cached
        result = load_result(inputs_hash,
                             ensure_list(cache_locations) + 
                             ensure_list(self._cache_dir))
        if result is not None:
            return result
        # start recording provenance
        aid = gen_uuid()
        self.audit({"@id": "pydra:{}".format(aid),
                    "startedAtTime": now()},
                   AUDIT_PROV)
          
        # Not cached        
        if self._cache_dir is None:
            self.cache_dir = mkdtemp()
        odir = self.cache_dir / inputs_hash
        odir.mkdir(parents=True, exist_ok=True)
        #check_runtime(self._runtime_requirements)
        #isolate inputs if files
        #cwd = os.getcwd()
        #id = record_provenance(self, env)
        #resources = start_monitor()
        result = Result(output=None)
        try:
            result.output = self._run_interface(**kwargs)
        except Exception as e:
            #record_error(self, e)
            raise
        finally:
            #resources = stop_monitor()
            pass
        #update_provenance(id, outputs, resources)
        save_result(odir, result)
        self.audit({"@id": "pydra:{}".format(aid),
                    "endedAtTime": now()},
                   AUDIT_PROV)
        
        return result

    def __call__(self, *args, cache_locations=None, **kwargs):
        return self.run(*args, cache_locations=cache_locations, **kwargs)

class FunctionTask(BaseTask):

    def __init__(self, func: ty.Callable, output_spec: ty.Optional[BaseSpec]=None,
                 audit_flags: bool=False,
                 messengers=None, messenger_args=None, **kwargs):
        self.input_spec = dc.make_dataclass(
            'Inputs', 
            [(val.name, val.annotation, val.default)
                  if val.default is not inspect.Signature.empty
                  else (val.name, val.annotation)
             for val in inspect.signature(func).parameters.values() 
             ] + [('_func', ty.Callable, func)],
            bases=(BaseSpec,))
        super(FunctionTask, self).__init__(inputs=kwargs, 
                                           audit_flags=audit_flags,
                                           messengers=messengers,
                                           messenger_args=messenger_args)
        if output_spec is None:
            if 'return' not in func.__annotations__:
                output_spec = dc.make_dataclass('Output', 
                                                [('out', ty.Any)],
                                                bases=(BaseSpec,))            
            else:
                return_info = func.__annotations__['return']
                output_spec = dc.make_dataclass(return_info.__name__, 
                                                return_info.__annotations__.items(),
                                                bases=(BaseSpec,))
        elif 'return' in func.__annotations__:
            raise NotImplementedError('Branch not implemented')
        self.output_spec = output_spec

    def _run_interface(self):
        inputs = dc.asdict(self.inputs)
        del inputs['_func']
        result = (self.inputs._func(**inputs))
        if not isinstance(result, tuple):
            result = (result,)
        outputs = self.output_spec(**{f.name:None for f in 
                                      dc.fields(self.output_spec)})
        return dc.replace(outputs, **dict(zip(self.output_names, list(result))))


def to_task(func_to_decorate):
    def create_func(**original_kwargs):
        function_task = FunctionTask(func=func_to_decorate,
                                     **original_kwargs)
        return function_task
    return create_func

class ShellTask(BaseTask):
    pass

class BashTask(ShellTask):
    pass

class MATLABTask(ShellTask):
    pass

@to_task
def testfunc(a:int, b:float=0.1) -> ty.NamedTuple('Output',  [('out', float)]):
    return a + b

@to_task
def no_annots(c, d):
    return c + d

if __name__ == '__main__':
    funky = testfunc(a=1, audit_flags=AUDIT_PROV, messengers=PrintMessenger())

    funky.inputs

    result = funky()
    result

    funky.output_names

    funky.result()

    funky.checksum

    funky.inputs.a = 2

    funky.checksum

    funky.result()

    funky()

    funky.result()

    natask = no_annots(c=17, d=3.2)

    res = natask.run()

    res

    res.output

    natask.inputs

