#-#  Copyright 2007-2015 Karlsruhe Institute of Technology
#-#
#-#  Licensed under the Apache License, Version 2.0 (the "License");
#-#  you may not use this file except in compliance with the License.
#-#  You may obtain a copy of the License at
#-#
#-#      http://www.apache.org/licenses/LICENSE-2.0
#-#
#-#  Unless required by applicable law or agreed to in writing, software
#-#  distributed under the License is distributed on an "AS IS" BASIS,
#-#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#-#  See the License for the specific language governing permissions and
#-#  limitations under the License.

import os, time, fnmatch, operator
from grid_control import utils
from grid_control.abstract import LoadableObject
from grid_control.exceptions import RethrowError, RuntimeError

class Job:
	__internals = ('wmsId', 'status')

	def __init__(self):
		self.state = Job.INIT
		self.nextstate = None
		self.attempt = 0
		self.history = {}
		self.wmsId = None
		self.submitted = 0
		self.changed = 0
		self.dict = {}


	def loadData(cls, name, data):
		try:
			job = Job()
			job.state = Job.str2enum(data.get('status', 'FAILED'))

			if 'id' in data:
				if not data['id'].startswith('WMSID'): # Legacy support
					data['legacy'] = data['id']
					if data['id'].startswith('https'):
						data['id'] = 'WMSID.GLITEWMS.%s' % data['id']
					else:
						wmsId, backend = tuple(data['id'].split('.', 1))
						data['id'] = 'WMSID.%s.%s' % (backend, wmsId)
				job.wmsId = data['id']
			if 'attempt' in data:
				job.attempt = data['attempt']
			if 'submitted' in data:
				job.submitted = data['submitted']
			if 'runtime' not in data:
				if 'submitted' in data:
					data['runtime'] = time.time() - float(job.submitted)
				else:
					data['runtime'] = 0
			if 'changed' in data:
				job.changed = data['changed']

			for key in range(1, job.attempt + 1):
				if ('history_' + str(key)).strip() in data:
					job.history[key] = data['history_' + str(key)]

			for i in cls.__internals:
				try:
					del data[i]
				except:
					pass
			job.dict = data
		except:
			raise RethrowError('Unable to parse data in %s:\n%r' % (name, data), RuntimeError)
		return job
	loadData = classmethod(loadData)


	def load(cls, name):
		try:
			data = utils.DictFormat(escapeString = True).parse(open(name))
		except:
			raise RuntimeError('Invalid format in %s' % name)
		return Job.loadData(name, data)
	load = classmethod(load)


	def getAll(self):
		data = self.dict
		data['status'] = Job.enum2str(self.state)
		data['attempt'] = self.attempt
		data['submitted'] = self.submitted
		data['changed'] = self.changed
		for key, value in self.history.items():
			data['history_' + str(key)] = value
		if self.wmsId != None:
			data['id'] = self.wmsId
			if self.dict.get('legacy', None): # Legacy support
				data['id'] = self.dict.pop('legacy')
		return data


	def set(self, key, value):
		self.dict[key] = value


	def get(self, key, default = None):
		return self.dict.get(key, default)


	def update(self, state):
		self.state = state
		self.changed = time.time()
		self.history[self.attempt] = self.dict.get('dest', 'N/A')


	def assignId(self, wmsId):
		self.dict['legacy'] = None # Legacy support
		self.wmsId = wmsId
		self.attempt = self.attempt + 1
		self.submitted = time.time()

utils.makeEnum(['INIT', 'SUBMITTED', 'DISABLED', 'READY', 'WAITING', 'QUEUED', 'ABORTED',
		'RUNNING', 'CANCELLED', 'DONE', 'FAILED', 'SUCCESS'], Job, useHash = True)


class JobClass:
	mkJobClass = lambda *fList: (reduce(operator.add, map(lambda f: 1 << f, fList)), fList)
	ATWMS = mkJobClass(Job.SUBMITTED, Job.WAITING, Job.READY, Job.QUEUED)
	RUNNING = mkJobClass(Job.RUNNING)
	PROCESSING = mkJobClass(Job.SUBMITTED, Job.WAITING, Job.READY, Job.QUEUED, Job.RUNNING)
	READY = mkJobClass(Job.INIT, Job.FAILED, Job.ABORTED, Job.CANCELLED)
	DONE = mkJobClass(Job.DONE)
	SUCCESS = mkJobClass(Job.SUCCESS)
	DISABLED = mkJobClass(Job.DISABLED)
	ENDSTATE = mkJobClass(Job.SUCCESS, Job.DISABLED)
	PROCESSED = mkJobClass(Job.SUCCESS, Job.FAILED, Job.CANCELLED, Job.ABORTED)


class JobDB(LoadableObject):
	def __init__(self, config, jobLimit = -1, jobSelector = None):
		self._dbPath = config.getWorkPath('jobs')
		self._jobMap = self.readJobs(jobLimit)
		if jobLimit < 0 and len(self._jobMap) > 0:
			jobLimit = max(self._jobMap) + 1
		(self.jobLimit, self.alwaysSelector) = (jobLimit, jobSelector)


	def readJobs(self, jobLimit):
		try:
			if not os.path.exists(self._dbPath):
				os.mkdir(self._dbPath)
		except IOError:
			raise RethrowError("Problem creating work directory '%s'" % self._dbPath)

		candidates = fnmatch.filter(os.listdir(self._dbPath), 'job_*.txt')
		(jobMap, log, maxJobs) = ({}, None, len(candidates))
		for idx, jobFile in enumerate(candidates):
			if (jobLimit >= 0) and (len(jobMap) >= jobLimit):
				utils.eprint('Stopped reading job infos! The number of job infos in the work directory (%d) ' % len(jobMap), newline = False)
				utils.eprint('is larger than the maximum number of jobs (%d)' % jobLimit)
				break
			try: # 2xsplit is faster than regex
				jobNum = int(jobFile.split(".")[0].split("_")[1])
			except:
				continue
			jobObj = Job.load(os.path.join(self._dbPath, jobFile))
			jobMap[jobNum] = jobObj
			if idx % 100 == 0:
				del log
				log = utils.ActivityLog('Reading job infos ... %d [%d%%]' % (idx, (100.0 * idx) / maxJobs))
		return jobMap


	def get(self, jobNum, default = None, create = False):
		if create:
			self._jobMap[jobNum] = self._jobMap.get(jobNum, Job())
		return self._jobMap.get(jobNum, default)


	def getJobsIter(self, jobSelector = None, subset = None):
		if subset == None:
			subset = xrange(self.jobLimit)
		if jobSelector and self.alwaysSelector:
			select = lambda *args: jobSelector(*args) and self.alwaysSelector(*args)
		elif jobSelector or self.alwaysSelector:
			select = utils.QM(jobSelector, jobSelector, self.alwaysSelector)
		else:
			for jobNum in subset:
				yield jobNum
			raise StopIteration
		for jobNum in subset:
			if select(jobNum, self.get(jobNum, Job())):
				yield jobNum


	def getJobs(self, jobSelector = None, subset = None):
		return list(self.getJobsIter(jobSelector, subset))


	def getJobsN(self, jobSelector = None, subset = None):
		counter = 0
		for jobNum in self.getJobsIter(jobSelector, subset):
			counter += 1
		return counter


	def commit(self, jobNum, jobObj):
		fp = open(os.path.join(self._dbPath, 'job_%d.txt' % jobNum), 'w')
		utils.safeWrite(fp, utils.DictFormat(escapeString = True).format(jobObj.getAll()))
#		if jobObj.state == Job.DISABLED:


	def __len__(self):
		return self.jobLimit
