#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime, timedelta, time as timed
import json
import os
import time

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue

from google.appengine.ext import ndb

from models import BooleanMessage
from models import StringMessage
from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import TeeShirtSize
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import Session
from models import SessionForm
from models import SessionForms
from models import SpeakerForm
from settings import WEB_CLIENT_ID
from settings import IOS_CLIENT_ID
from utils import getUserId

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": [ "Default", "Topic" ],
}

FIELDS = {
    'CITY': 'city',
    'TOPIC': 'topics',
    'MONTH': 'month',
    'MAX_ATTENDEES': 'maxAttendees',
}

OPERATORS = {
    'EQ':   '=',
    'GT':   '>',
    'GTEQ': '>=',
    'LT':   '<',
    'LTEQ': '<=',
    'NE':   '!='
    }

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey = messages.StringField(1, required=True),
)

SESSION_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey = messages.StringField(1, required=True),
    typeOfSession = messages.StringField(2)
)

SPEAKER_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    speaker = messages.StringField(1, required=True)
)

WISHLIST_POST_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey = messages.StringField(1, required=True)
)

MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
FEATURED_SPEAKER_KEY = "FEATURED_SPEAKER"
ANNOUNCEMENT_TPL = ('Last chance to attend! The following conferences '
                    'are nearly sold out: %s')

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

@endpoints.api( name='conference',
                version='v1',
                allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID, IOS_CLIENT_ID],
                scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm"""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent"""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get user id by calling getUserId(user)
        # create a new key of kind Profile from the id
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)

        # get the entity from datastore by using get() on the key
        profile = p_key.get()

        # profile = None
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
                )

            # save the profile to datastore
            profile.put()
        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first"""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))

            # put the modified profile to datastore
            prof.put()
        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)

# - - - Conference objects - - - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm"""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf

    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request"""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        # both for data model & outbound Message
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
            setattr(request, "seatsAvailable", data["maxAttendees"])

        # make Profile Key from user ID
        p_key = ndb.Key(Profile, user_id)
        # allocate new Conference ID with Profile key as parent
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        # make Conference key from ID
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference & return (modified) ConferenceForm
        Conference(**data).put()

        taskqueue.add(params={'email': user.email(), 'conferenceInfo': repr(request)},
                        url='/tasks/send_confirmation_email')
        return request


    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference"""
        return self._createConferenceObject(request)

    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)"""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey
            )
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user"""
        # make sure user is authenticated
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization Required.')
        user_id = getUserId(user)

        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()

        # return a set of conference form objects per Conference
        return ConferenceForms(
            items = [self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                    path='filterPlayground', http_method='GET', name='filterPlayground')
    def filterPlayground(self, request):
        q = Conference.query()

        # 1. City equal to "London"
        q = q.filter(Conference.city == "London")

        # 2. Topic equals "Medical Innovations"
        q = q.filter(Conference.topics == "Medical Innovations")

        # 3. Order by conference names
        q = q.order(Conference.maxAttendees)
        q = q.order(Conference.name)
        # 4. Filter by maxAttendees
        q = q.filter(Conference.maxAttendees > 10)

        return ConferenceForms(
            items = [self._copyConferenceToForm(conf, "") for conf in q]
        )

    def _getQuery(self, request):
        """Return formatted query from the submitted filters"""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        # Create a query using all of the submitted filters
        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters"""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid \
                    field or operator.")

            if filtr["operator"] != "=":
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is \
                        allowed on only one field.")
                else:
                    inequality_field = filtr["field"]
            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)

    @endpoints.method(ConferenceQueryForms, ConferenceForms,
                    path='queryConferences', http_method='POST',
                    name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences"""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # Return individual ConferenceForm object per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
            conferences]
        )

    # The function is changing two different kind of entities, Profile and Conference.
    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference"""
        retval = None
        prof = self._getProfileFromUser() # get user profile

        # check if conf exists give websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add him
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException("You have already registered for this \
                conference")

            # check if seats available
            if conf.seatsAvailable <= 0:
                raise ConflictException("There are not seats available for \
                this conference.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # chek if user already registered
            if wsck in prof.conferenceKeysToAttend:
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to db and return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                    path='conferences/attending', http_method='GET',
                    name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for"""
        prof = self._getProfileFromUser()
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organisers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier use
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])\
            for conf in conferences])

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
        path='conference/{websafeConferenceKey}', http_method='POST',
        name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference"""
        return self._conferenceRegistration(request)

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
        path='conference/{websafeConferenceKey}', http_method='DELETE',
        name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference"""
        return self._conferenceRegistration(request, reg=False)

    @endpoints.method(CONF_GET_REQUEST, SessionForms,
        path='conference/{websafeConferenceKey}/sessions', http_method='GET',
        name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Get all sessions from a specific conference"""

        # fetch existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()

        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException('No conference found with \
                key: %s' % request.websafeConferenceKey)

        # create ancestor query for all key matches for this conference
        sessions = Session.query(ancestor=conf.key)

        # return set of SessionForm objects per Session
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

    @endpoints.method(SESSION_GET_REQUEST, SessionForms,
        path='conference/{websafeConferenceKey}/sessions/by_type/{typeOfSession}',
        http_method='GET', name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """Get all sessions with a specific type from a specific conference"""

        typeOfSession = getattr(request, 'typeOfSession')

        # fetch existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()

        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException('No conference found with \
                key %s' % request.websafeConferenceKey)

        # create ancestor query for all key matches for this conference
        sessions = Session.query(Session.typeOfSession == typeOfSession,
            ancestor=ndb.Key(urlsafe=request.websafeConferenceKey))

        # return set of ConferenceForm objects per conference
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
        http_method='GET', name='getLastChanceConferences')
    def getLastChanceConferences(self, request):
        """Get all conferences with a last chance to attend"""
        conferences_query = Conference.query()
        # filter with seats below or equal to 2
        conferences_query = conferences_query.filter(Conference.seatsAvailable <= 2)\
        .filter(Conference.seatsAvailable > 0)

        return ConferenceForms(
            items = [self._copyConferenceToForm(conf, "") for conf in conferences_query]
        )

    @endpoints.method(message_types.VoidMessage, SessionForms, http_method='GET',
        name='getTodaySessions')
    def getTodaySessions(self, request):
        """Return all sessions happening today for the conferences that the user
        has subscribed to"""

        today_sessions = []

        for websafeConferenceKey in prof.conferenceKeysToAttend:

            # get all sessions for each conference and filter them to get
            # sessions with date equals today's date
            sessions = Session.query(ancestor=ndb.Key(urlsafe=websafeConferenceKey))\
                .filter(Session.date > datetime.today() - timedelta(days=1))\
                .filter(Session.date < datetime.today() + timedelta(days=1))
            for session in sessions:
                today_sessions.append(session)

        return SessionForms(
            items=[self._copySessionToForm(today_session) for today_session in today_sessions]
        )

    @endpoints.method(SPEAKER_GET_REQUEST, SessionForms,
        path='sessions/speaker/{speaker}', http_method='GET', name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Return all sessions given by a certain speaker, across all conferences"""

        # query sessions by speaker
        sessions = Session.query(Session.speaker == request.speaker)

        # return set of ConferenceForm objects per Conference
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

    @endpoints.method(SessionForm, SessionForm, path='createSession', http_method='POST',
        name='createSession')
    def createSession(self, request):
        """The organizer of the conference can use this method to create a session"""
        return self._createSessionObject(request)

    @endpoints.method(WISHLIST_POST_REQUEST, SessionForm, path='profile/wishlist',
        http_method='POST', name='addSessionToWishlist')
    def addSessionToWishList(self, request):
        """Saves a session to a user's wishlist"""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required.')

        # fetch and check session
        session = ndb.Key(urlsafe=request.websafeSessionKey).get()

        # check that session exists
        if not session:
            raise endpoints.NotFoundException('No session found with \
            key: %s' % request.websafeSessionKey)

        # fetch profile
        prof = self._getProfileFromUser()

        # check if session already added to wishlist
        if session.key in prof.sessionsToAttend:
            raise endpoints.BadRequestException('Session already saved to \
                wishlist: %s' % request.websafeSessionKey)

        # append to user profile's wishlist
        prof.sessionsToAttend.append(session.key)
        prof.put()

        return self._copySessionToForm(session)

    @endpoints.method(WISHLIST_POST_REQUEST, BooleanMessage, path='profile/wishlist',
        http_method='DELETE', name='deleteSessionInWishlist')
    def deleteSessionInWishList(self, request):
        """Delete a session from a user's wishlist"""
        retval = None
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required.')

        session = ndb.Key(urlsafe=request.websafeSessionKey).get()

        # check that session exists
        if not session:
            raise endpoints.NotFoundException('No session found with \
            key: %s' % request.websafeSessionKey)

        # fetch profile
        prof = self._getProfileFromUser()

        # check if session already added to wishlist
        if session.key in prof.sessionsToAttend:
            prof.sessionsToAttend.remove(session.key)
            prof.put()
            retval = True
        else:
            retval = False

        return BooleanMessage(data=retval)

    @endpoints.method(message_types.VoidMessage, SessionForms, path='profile/wishlist',
        http_method='GET', name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """Return a user's wishlist of sessions"""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required.')

        # fetch profile and wishlist
        prof = self._getProfileFromUser()
        session_keys = prof.sessionsToAttend
        sessions = [session_key.get() for session_key in session_keys]

        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

    @endpoints.method(message_types.VoidMessage, SessionForms, http_method='GET',
        name='getNonWorkshopsBeforeSevenPm')
    def getNonWorkshopsBeforeSevenPm(self, request):
        """Get all sessions that are not workshops happening before 7 pm"""
        sessions = Session.query(ndb.AND(Session.startTime != None,
            Session.startTime <= timed(hour=19)))

        filtered_sessions = []
        for session in sessions:
            # for each session check that that session type is not a
            # 'workshop' or 'Workshop'
            if not 'workshop' in session.typeOfSession and not 'Workshop' in session.typeOfSession:
                filtered_sessions.append(session)

        return SessionForms(
            items=[self._copySessionToForm(session) for session in filtered_sessions]
        )


# - - - Session objects  - - - - - - - - - - - - - - - - - - - -

    def _copySessionToForm(self, session):
        """Copy relevant fields from Session to SessionForm"""
        sf = SessionForm()
        for field in sf.all_fields():
            if hasattr(session, field.name):
                 # convert Date and Time to date string
                if field.name in ['startTime', 'date']:
                    setattr(sf, field.name, str(getattr(session, field.name)))
                # if fields are not Date or Time, just copy the string
                else:
                    setattr(sf, field.name, getattr(session, field.name))
            elif field.name == 'websafeKey':
                setattr(sf, field.name, session.key.urlsafe())
        sf.check_initialized()
        return sf

    def _createSessionObject(self, request):
        """Create Session object"""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Session 'name' field required.")

        # fetch and check conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()

        if not conf:
            raise endpoints.NotFoundException('No conference found with \
            key %s') % request.websafeConferenceKey

        # check that the user is the organizer of the conferrence
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException("This user can't add sessions to \
            the conference because he is not the conference organizer.")

        # copy SessionForm/ProtoRPC message into a dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # convert dates from strings to Date objects
        if data['date']:
            data['date'] = datetime.strptime(data['date'][:10], "%Y-%m-%d").date()

        # convert time from strings to Time objects
        if data['startTime']:
            data['startTime'] = datetime.strptime(data['startTime'][:5], "%H:%M").time()

        parent_key = conf.key
        child_id = Session.allocate_ids(size=1, parent=parent_key)[0]
        child_key = ndb.Key(Session, child_id, parent=parent_key)
        data['key'] = child_key

        del data['websafeConferenceKey']
        del data['websafeKey']

        Session(**data).put()

        # When a new session is added to a conference, check the speaker.
        # If there is more than one session by this speaker at this conference,
        # add a new Memcache entry that features the speaker and session names.
        speaker = data['speaker']

        taskqueue.add(
            params  = {'speaker': speaker,
                       'conference_key': request.websafeConferenceKey},
            url     = '/tasks/set_featured_speaker',
            method  = 'GET'
        )

        return self._copySessionToForm(child_key.get())

# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """
        Create announcement and assign to memcache; used by memcache cron job
        and putAnnouncement()
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = ANNOUNCEMENT_TPL % (
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement

    @endpoints.method(message_types.VoidMessage, StringMessage,
                        path='conference/announcement/get',
                        http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return announcement from memcache"""
        return StringMessage(data=memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY) or "")

# - - - Featured Speaker - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheFeaturedSpeaker(speaker, conference_key):
        """
        Cache speaker data and the Sessions of this speaker if he is a
        featured one (has more than one session)
        """
        parent_key = ndb.Key(urlsafe=conference_key)
        speaker_sessions_objects = Session.query(Session.speaker == speaker, ancestor=parent_key)
        speaker_sessions_names = [speaker_session_object.name for speaker_session_object in speaker_sessions_objects]
        if(len(speaker_sessions_names) > 1):
            speaker_sessions = ', '.join(speaker_sessions_names)
            cache_data = {}
            cache_data['speaker'] = speaker
            cache_data['speaker_sessions'] = speaker_sessions
            memcache.set(FEATURED_SPEAKER_KEY+conference_key, cache_data)

    @endpoints.method(CONF_GET_REQUEST, SpeakerForm,
                        path='conference/featured_speaker/get',
                        http_method='GET', name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Return featured speaker for a specific conference from memcache"""
        conference_key = request.websafeConferenceKey

        # get data from memcache
        data = memcache.get(FEATURED_SPEAKER_KEY+conference_key)

        sessions = []
        speaker_sessions = []
        speaker = None

        # if data exists and has keys for 'speaker', 'speaker_sessions'
        # and 'conference_key' then get the 'speaker' & 'speaker_sessions' values

        if data and data.has_key('speaker') and data.has_key('speaker_sessions'):
            speaker = data['speaker']
            speaker_sessions = data['speaker_sessions']
        else:
            # if data does not exist or keys do not exist, then get the data of
            #the next upcoming session to return it
            upcoming_session = Session.query(Session.date >= datetime.now())\
                                .order(Session.date, Session.startTime).get()
            if upcoming_session:
                speaker = upcoming_session.speaker
                sessions = Session.query(Session.speaker == speaker)
                speaker_sessions = ', '.join([session.name for session in sessions])

        speaker_form = SpeakerForm()
        for field in speaker_form.all_fields():
            if field.name == 'speaker_sessions':
                setattr(speaker_form, field.name, speaker_sessions)
            elif field.name == 'speaker':
                setattr(speaker_form, field.name, speaker)
            elif field.name == 'conference_key':
                setattr(speaker_form, field.name, conference_key)
        speaker_form.check_initialized()
        return speaker_form

# registers API
api = endpoints.api_server([ConferenceApi])
