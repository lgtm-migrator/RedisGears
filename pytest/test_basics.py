from RLTest import Env
import yaml
import time


def getConnectionByEnv(env):
    conn = None
    if env.env == 'oss-cluster':
        env.broadcast('rg.refreshcluster')
        conn = env.envRunner.getClusterConnection()
    else:
        conn = env.getConnection()
    return conn


class testBasic:
    def __init__(self):
        self.env = Env()
        conn = getConnectionByEnv(self.env)
        for i in range(100):
            conn.execute_command('set', str(i), str(i))

    def testBasicQuery(self):
        id = self.env.cmd('rg.pyexecute', "gearsCtx().map(lambda x:str(x)).collect().run()", 'UNBLOCKING')
        res = self.env.cmd('rg.getresultsblocking', id)
        for i in range(100):
            self.env.assertContains('%s : %s' % (str(i), str(i)), res[1])
        self.env.cmd('rg.dropexecution', id)

    def testBasicFilterQuery(self):
        id = self.env.cmd('rg.pyexecute', 'gearsCtx().filter(lambda x: int(str(x["value"])) >= 50).map(lambda x:str(x)).collect().run()', 'UNBLOCKING')
        res = self.env.cmd('rg.getresultsblocking', id)
        for i in range(50, 100):
            self.env.assertContains('%s : %s' % (str(i), str(i)), res[1])
        self.env.cmd('rg.dropexecution', id)

    def testBasicMapQuery(self):
        id = self.env.cmd('rg.pyexecute', 'gearsCtx().map(lambda x: x["value"]).map(lambda x:str(x)).collect().run()', 'UNBLOCKING')
        res = self.env.cmd('rg.getresultsblocking', id)
        res = [yaml.load(r) for r in res[1]]
        self.env.assertEqual(set(res), set([i for i in range(100)]))
        self.env.cmd('rg.dropexecution', id)

    def testBasicGroupByQuery(self):
        id = self.env.cmd('rg.pyexecute', 'gearsCtx().'
                                          'map(lambda x: {"key":x["key"], "value": 0 if int(str(x["value"])) < 50 else 100}).'
                                          'groupby(lambda x: str(x["value"]), lambda key, a, vals: 1 + (a if a else 0)).'
                                          'map(lambda x:str(x)).collect().run()', 'UNBLOCKING')
        res = self.env.cmd('rg.getresultsblocking', id)
        self.env.assertContains("100 : 50", res[1])
        self.env.assertContains("0 : 50", res[1])
        self.env.cmd('rg.dropexecution', id)

    def testBasicAccumulate(self):
        id = self.env.cmd('rg.pyexecute', 'gearsCtx().'
                                          'map(lambda x: int(str(x["value"]))).'
                                          'accumulate(lambda a,x: x + (a if a else 0)).'
                                          'collect().'
                                          'accumulate(lambda a,x: x + (a if a else 0)).'
                                          'map(lambda x:str(x)).run()', 'UNBLOCKING')
        res = self.env.cmd('rg.getresultsblocking', id)[1]
        self.env.assertEqual(sum([a for a in range(100)]), int(res[0]))
        self.env.cmd('rg.dropexecution', id)


def testFlatMap(env):
    conn = getConnectionByEnv(env)
    conn.execute_command('lpush', 'l', '1', '2', '3')
    id = env.cmd('rg.pyexecute', "gearsCtx()."
                                 "flatmap(lambda x: x['value'])."
                                 "collect().run()", 'UNBLOCKING')
    res = env.cmd('rg.getresultsblocking', id)
    env.assertEqual(set(res[1]), set(['1', '2', '3']))
    env.cmd('rg.dropexecution', id)


def testLimit(env):
    conn = getConnectionByEnv(env)
    conn.execute_command('lpush', 'l', '1', '2', '3')
    id = env.cmd('rg.pyexecute', "gearsCtx()."
                                 "flatmap(lambda x: x['value'])."
                                 "limit(1).collect().run()", 'UNBLOCKING')
    res = env.cmd('rg.getresultsblocking', id)
    env.assertEqual(len(res[1]), 1)
    env.cmd('rg.dropexecution', id)


def testRepartitionAndWriteOption(env):
    conn = getConnectionByEnv(env)
    conn.execute_command('set', 'x', '1')
    conn.execute_command('set', 'y', '2')
    conn.execute_command('set', 'z', '3')
    id = env.cmd('rg.pyexecute', "gearsCtx()."
                                 "repartition(lambda x: x['value'])."
                                 "foreach(lambda x: redisgears.executeCommand('set', x['value'], x['key']))."
                                 "map(lambda x : str(x)).collect().run()", 'UNBLOCKING')
    res = env.cmd('rg.getresultsblocking', id)[1]
    env.assertContains("x : 1", str(res))
    env.assertContains("y : 2", str(res))
    env.assertContains("z : 3", str(res))
    env.assertEqual(conn.execute_command('get', '1'), 'x')
    env.assertEqual(conn.execute_command('get', '2'), 'y')
    env.assertEqual(conn.execute_command('get', '3'), 'z')
    env.cmd('rg.dropexecution', id)


def testBasicStream(env):
    conn = getConnectionByEnv(env)
    res = env.cmd('rg.pyexecute', "gearsCtx()."
                                  "repartition(lambda x: 'values')."
                                  "foreach(lambda x: redisgears.executeCommand('lpush', 'values', x['value']))."
                                  "register('*')", 'UNBLOCKING')
    env.assertEqual(res, 'OK')
    if(res != 'OK'):
        return
    time.sleep(0.5)  # make sure the execution reached to all shards
    conn.execute_command('set', 'x', '1')
    conn.execute_command('set', 'y', '2')
    conn.execute_command('set', 'z', '3')
    res = []
    while len(res) < 3:
        res = env.cmd('rg.dumpexecutions')
    for e in res:
        env.broadcast('rg.getresultsblocking', e[1])
        env.cmd('rg.dropexecution', e[1])
    env.assertEqual(set(conn.lrange('values', '0', '-1')), set(['1', '2', '3']))


def testBasicStreamProcessing(env):
    conn = getConnectionByEnv(env)
    res = env.cmd('rg.pyexecute', "gearsCtx('StreamReader')."
                                  "flatmap(lambda x: [(a[0], a[1]) for a in x.items()])."
                                  "repartition(lambda x: x[0])."
                                  "foreach(lambda x: redisgears.executeCommand('set', x[0], x[1]))."
                                  "map(lambda x: str(x))."
                                  "register('stream1')", 'UNBLOCKING')
    env.assertEqual(res, 'OK')
    if(res != 'OK'):
        return
    time.sleep(0.5)  # make sure the execution reached to all shards
    env.cmd('XADD', 'stream1', '*', 'f1', 'v1', 'f2', 'v2')
    res = []
    while len(res) < 1:
        res = env.cmd('rg.dumpexecutions')
    for e in res:
        env.broadcast('rg.getresultsblocking', e[1])
        env.cmd('rg.dropexecution', e[1])
    env.assertEqual(conn.get('f1'), 'v1')
    env.assertEqual(conn.get('f2'), 'v2')
