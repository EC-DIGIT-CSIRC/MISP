#!/usr/bin/python2 -O
#  -*- coding:utf-8 -*-

"""
	Retrieve events from Facebook Threat Exchange and push them to MISP

	Author: David DURVAUX
	Copyright: EC DIGIT CSIRC (European Commission) - February 2017

	!! POC version !! 

	Note - Python API dropped - easier to use web queries
		API well described here: 
		https://developers.facebook.com/docs/threat-exchange/reference/apis/threat-indicators/v2.8

	TODO:
	-----
		- fine control over MISP event creation
		- switch to Python 3.4 (see warnings)
		- add command line parameters (to control behaviour, pass auth to FB...)
		- remove all internal structure to JSON file
		- keep reference to original ID in TE to avoid duplicates (history file)
		- handle changes in status (UNKNOWN, MALICIOUS, NONMALICIOUS) (update function)
		- implement auto publish

	Version: 0.000000002 :)

	Thanks to
		- Facebook for ThreatExchange
		- MISP for MISP ;)
		- Raphael Vinot and Alexandre Dulaunoy for tips and tricks
"""
import os
import ast
import sys
import json
import time
import urllib
import argparse
import requests

# Import MISP API
from pymisp import PyMISP, MISPEvent, EncodeUpdate

# Import configuration.py with API keys
import configuration

# --------------------------------------------------------------------------- #

class FacebookTE():
	app_id = None
	app_secret = None
	proxy = None


	def __init__(self, app_id, app_secret, proxy=None):
		self.app_id = app_id
		self.app_secret = app_secret
		self.proxy = proxy


	def retrieveMalwareAnalysesLastNDays(self, numbdays):
		"""
			Retrieve the list of malware analysis published
			for the last numbdays.
		"""
		end_time = int(time.time()) # NOW
		start_time = end_time - numbdays * (24 * 3600)
		
		query_params = {
    		'since' : start_time,
    		'until' : end_time
    	}
		
		return json.loads(self.__query_threat_exchange__("malware_analyses", query_params))
	
	
	def retrieveThreatIndicatorsLastNDays(self, numbdays):
		"""
			Retrieve the list of published indicators
			for the last numbdays.
		"""	
		end_time = int(time.time()) # NOW
		start_time = end_time - numbdays * (24 * 3600) # NOW - 24h
	
		query_params = {
    		'since' : start_time,
    		'until' : end_time
    	}
		
		return json.loads(self.__query_threat_exchange__("threat_indicators", query_params))


	def retrieveThreatDescriptorsLastNDays(self, numbdays):
		"""
			Retrieve the list of published indicators
			for the last numbdays.
		"""
		end_time = int(time.time()) # NOW
		start_time = end_time - numbdays * (24 * 3600) # NOW - 24h
	
		query_params = {
    		'since' : start_time,
    		'until' : end_time
    	}
		
		return json.loads(self.__query_threat_exchange__("threat_descriptors", query_params))


	def retrieveEvent(self, eventid, params={}):
		"""
			Sample Event:
			{
    			"added_on": "2017-02-09T14:26:57+0000",
    			"description": "IDS Detected Spam",
    			"id": "1234567890",
    			"indicator": {
        			"id": "1234567890",
        			"indicator": "11.22.33.44",
        			"type": "IP_ADDRESS"
    			},
    			"owner": {
        			"email": "foo\\u0040bar.com",
        			"id": "987654321",
        			"name": "FooBar ThreatExchange"
    			},
    			"privacy_type": "VISIBLE",
    			"raw_indicator": "11.22.33.44",
    			"share_level": "GREEN",
    			"status": "MALICIOUS",
    			"type": "IP_ADDRESS"
			}
		"""
		try:
			params['access_token'] = self.app_id + '|' + self.app_secret
			uparams = urllib.urlencode(params)

			uri = 'https://graph.facebook.com/%s?' % eventid
			request = requests.get(uri + uparams)
			return json.dumps(ast.literal_eval(request.text), sort_keys=True,indent=4,separators=(',', ': '))
		except Exception as e:
			print("Impossible to retrieve event %s" % eventid)
			print(e)
		return None


	def __query_threat_exchange__(self, query_type, params={}):
		"""
			Generic function to query Facebook with URL format:
			'https://graph.facebook.com/v2.8/<query_type>?' + query_params

			checkout: 
				https://developers.facebook.com/docs/threat-exchange/reference/apis/v2.8

			params = hashtable containing all query option EXCEPT auth which will be added
			         by this function
		"""
		try:
			params['access_token'] = self.app_id + '|' + self.app_secret
			uparams = urllib.urlencode(params)

			uri = 'https://graph.facebook.com/v2.8/%s?' % query_type
			request = requests.get(uri + uparams, proxies=self.proxy)
			return json.dumps(ast.literal_eval(request.text), sort_keys=True,indent=4,separators=(',', ': '))
		except Exception as e:
			print("Impossible to query %s" % query_type)
			print(e)
		return None

# --------------------------------------------------------------------------- #

class MISP():
	"""
		Handle manipulation of event into MISP

		TODO:
		    - use MISPEvent for creating MISP events
		         check https://www.circl.lu/assets/files/misp-training/luxembourg2017/4.1-pymisp.pdf
		    - use PyTaxonomies for Tags
	"""
	api = ""
	url = ""
	misp = ""
	proxies = None
	sslCheck = False       # Not recommended
	debug = False          # Enable debug mode
	score = {              # Map Facebook Threat Exchange "status" to a score to determine if event will be made in MISP
		0 : "NON MALICIOUS",
		1 : "UNKNOWN",
		2 : "SUSPICIOUS",
		3 : "MALICIOUS"
	}
	badness_threshold = 1  # Minimum score in score table to create a MISP event
	published = False      # Default state of MISP created object

	# ThreatExchange type -> MISP
	# see IndicatorType object
	# 	https://developers.facebook.com/docs/threat-exchange/reference/apis/indicator-type/v2.8
	type_map = {}          # Map of Facebook Threat Exchange to MISP types
	share_levels = {}      # Map of sharing levels (TLP -> TLP)
	extra_tag = None       # Extra tag to add to all imported events (! no check of consistency !)
	privacy_levels = {}    # Map the privacy_type of Facebook Threat Exchange to Sharing Group ID of MISP

	# ----------------------------------------------------------------------- #

	def __init__(self, url, api, proxies=None):
		self.url = url
		self.api = api
		self.proxies = proxies
		self.misp = PyMISP(self.url, self.api, ssl=self.sslCheck, out_type='json', debug=self.debug, proxies=self.proxies, cert=None)
		return


	def convertTEtoMISP(self, teevent):
		"""
			Convert a ThreatExchange entry to MISP entry
		"""
		# Create empty event
		mispevt = MISPEvent()
		mispevt.info = "[Facebook ThreatExchange]"
		mispevt.distribution = 0
		mispevt.sharing_group_id = self.privacy_levels[teevent["privacy_type"]]

		# Check if event is to be kept
		if "status" in teevent.keys() and teevent["status"] in self.score.keys() and self.score[teevent["status"]] < self.badness_threshold :
			print("IGNORE EVENT %s due to status (%s)" % (teevent, teevent["status"]))
			return None

		# Add indicator to event
		if "raw_indicator" in teevent.keys():
			if "type" in teevent.keys():
				if teevent["type"] in self.type_map.keys():
					indicator = teevent["raw_indicator"].replace("\\", "")
					mispevt.add_attribute(self.type_map[teevent["type"]] , indicator) # not to brutal??
				else:
					print("WARNING: TYPE %s SHOULD BE ADDED TO MAPPING" % teevent["type"])
		else:
			print("WARNING, event %s does not contains any indicator :(" % teevent)
			return None # don't create event without content!

		# Add a category
		mispevt.category = "Network activity"

		# Enrich description
		if "description" in teevent.keys():
			mispevt.info = mispevt.info + " - %s" % teevent["description"]
		if "owner" in teevent.keys() and "name" in teevent["owner"].keys():
			owner = teevent["owner"]["name"]
			email = teevent["owner"]["email"].replace("\\u0040", "@")
			mispevt.info = mispevt.info + " - by %s (%s)" % (owner, email)

		# Add sharing indicators (tags)
		if "share_level" in teevent.keys():
			if teevent["share_level"] in self.share_levels.keys():
				mispevt.Tag.append(self.share_levels[teevent["share_level"]])
			else:
				print("WARNING: SHARING LEVEL %s SHOULD BE ADDED TO MAPPING" % teevent["share_level"])
		if self.extra_tag is not None:
			mispevt.Tag.append(self.extra_tag)

		# all done :)
		evtid = teevent["id"]
		return [evtid, mispevt]


	def createEvent(self, mispevent):
		"""
			Create a new event in MISP using a hash table structure describing the event
		"""
		if event is None:
			return None

		# Not empty event
		jevent = json.dumps(mispevt, cls=EncodeUpdate)
		misp_event = self.misp.add_event(jevent)
		mispid = misp_event["id"]
		return mispid


	def saveMapping(self, mapfile="./mapping.json"):
		"""
			Save internal mapping definition
		"""
		mappings = {
			"Sharing"   : self.share_levels,
			"Type"      : self.type_map,
			"Extra-Tag" : self.extra_tag,
			"Privacy"   : self.privacy_levels
		}
		try:
			fd = open(mapfile, "w")
			json.dump(mappings, fd, sort_keys=True,indent=4,separators=(',', ': '))
			fd.close()
		except Exception as e:
			print("IMPOSSIBLE TO SAVE MAPPINGS to %s" % mapfile)
			print(e)
		return


	def loadMapping(self, mapfile="./mapping.json"):
		"""
			Restore internal mapping from saved JSON file
		"""
		try:
			fd = open(mapfile, "r")
			mappings = json.load(fd)
			if "Sharing" in mappings.keys():
				self.share_levels = mappings["Sharing"]
			if "Type" in mappings.keys():
				self.type_map = mappings["Type"]
			if "Extra-Tag" in mappings.keys():
				self.extra_tag = mappings["Extra-Tag"]
			if "Privacy" in mappings.keys():
				self.privacy_levels = mappings["Privacy"]
			fd.close()
		except Exception as e:
			print("IMPOSSIBLE TO LOAD MAPPINGS from %s" % mapfile)
			print(e)
		return

# --------------------------------------------------------------------------- #

def fromFacebookToMISP(mapfile="./mapping.json", histfile="./history.json"):
	# Open connection to MISP w/ proxy handling if required
	proxies = None
	if configuration.MISP_PROXY:
		proxies = configuration.PROXIES
	misp = MISP(configuration.MISP_URI, configuration.MISP_API, proxies)
	if mapfile is not None:
		misp.loadMapping(mapfile)

	# Connect to Facebook Threat Exchange	
	proxies = None
	if configuration.TX_PROXY:
		proxies = configuration.PROXIES
	fb = FacebookTE(configuration.TX_APP_ID, configuration.TX_APP_SECRET, proxies)

	# Load history
	history = {}
	try:
		fd = open(histfile, "r")
		history = json.load(fd)
		fd.close()
	except Exception as e:
		print("ERROR: impossible to load history from %s" % histfile)
		print(e)

	# Retrieve event from Facebook
	threats = fb.retrieveThreatDescriptorsLastNDays(1)
	for event in threats["data"]:
		[teevtid, mispevt] = misp.convertTEtoMISP(event)
		if(teevtid not in history.keys():
			mispid = misp.createEvent(mispevt)
			history[teevtid] = mispid
		else:
			print("EVENT: %d already in MISP under ID: %d" % (teevtid, history[teevtid]))
			print("DEBUG -- need to implement an update function -- TODO!!") # DEBUG / TODO

	# Save history
	try:
		fd = open(histfile, "w")
		json.dump(history, fd, sort_keys=True,indent=4,separators=(',', ': '))
		fd.close()
	except Exception as e:
		print("ERROR: impossible to save history to %s" % histfile)
		print(e)

	# All done ;)
	return

"""
    Main function
"""
def main():
	# State
	mapping = None
	history = None

	# Parse command line arguments
	parser = argparse.ArgumentParser()
	parser.add_argument('-m', '--mapping', action='store', dest='mapping', help='Path to JSON mapping file.  By default, ./mapping.json', default='./mapping.json')
	parser.add_argument('-H', '--history', action='store', dest='history', help='Path to JSON history file.  By default, ./history.json', default='./history.json')

	# Parse arguments and configure the script
	arguments = parser.parse_args()
	if arguments.mapping:
		if not os.path.isfile(arguments.mapping):
			print("ERROR: %s has to be an existing mapping file!" % arguments.mapping)
			parser.print_help()
			sys.exit(-1)
		else:
			mapping = arguments.mapping

	# TODO - handle the other way round
	fromFacebookToMISP(mapping)

	# All done ;)
	return

# --------------------------------------------------------------------------- #

"""
   Call main function
"""
if __name__ == "__main__":
    
    # Create an instance of the Analysis class (called "base") and run main 
    main()

# That's all folks ;)