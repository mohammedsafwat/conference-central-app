# Conference Central Application Project

### About the project

Conference Central Application is a cloud-based API server to support a provided
conference organization application that exists on the web as well as a native
iOS and Android applications too.
The API supports the following functionality found within the app: user
authentication, user profiles, conference information and various manners in which
to query the data.

The application is hosted on appspot. You can access it from
[here](https://python-scalable-app-1186.appspot.com).

### How to run the project locally

1. Clone the project repository.
2. Update the value of `application` in `app.yaml` to the app ID for your application
registered on the App Engine admin console.
3. Update the client IDs for the apps that you want the cloud-based API server to support
inside `settings.py`.
4. Update the value of CLIENT_ID in `static/js/app.js` to the Web Client ID that
you had from step 2.
5. From 'Google App Engine Launcher' application choose 'Add Existing Application'
from the 'File' menu.
6. Press the 'Run' button for the selected application from 'Google App Engine Launcher'.
7. Visit your application at localhost:8080 (8080 is the default port).
8. You can access the API explorer for your application by visiting
http://localhost:8081/_ah/api/explorer

### Improvements
#### 1. Adding Sessions to a Conference

The following endpoints are used to support managing sessions for conferences:
- `createSession`: The organizer of the conference can use this method to create a session.
- `getConferenceSessions`: Get all sessions from a specific conference.
- `getConferenceSessionsByType`: Get all sessions with a specific type from a specific conference.
- `getSessionsBySpeaker`: Return all sessions given by a certain speaker, across all conferences

### Explanation for design choices

- A `Session` object was designed to be a child of any `Conference` object. This allows to have
one `conference` linked to many `session` objects, and `session` objects can
be queried by their `conference` ancestor.

- `Session` representation contains the following properties:
  - `name`: A String property to hold the name of the session. It's a required
  field because each session must have a name.
  - `highlights`: A String property to hold the highlights description of the
  session.
  - `speaker`: A String property to hold the speaker name. That will be a required
  field too. Speakers under each session are defined as string types because I didn't
  want to complicate the relationships at this point by creating a separate entity
  for each speaker. In future, I'll do that because I'll add more features to query
  all of the speakers and get more info about each one like the speaker's name,
  current job, photo, etc.
  - `duration`: An Integer property to hold the session's duration.
  - `typeOfSession`: A (repeated) string property to hold the type of the
  session. This is represented like an array of strings, each string holds a
  value for a separate session type.
  - `date`: A Date property to hold the session's date.
  - `startTime`: A Time property to hold the session's start time.

#### 2. Adding Sessions to User Wishlist

- Each user can save his wishlist under his `Profile`. The `Profile` has a
repeated key property field, named `sessionsToAttend`. To be able to manage `Session`
objects to be added, removed or queried from the wishlist, the below endpoints were defined:
- `addSessionToWishList`: Adds a session to the user's wishlist. This is done
by using the websafeKey returned for each Session.
- `getSessionsInWishlist`: Returns a user's wishlist of sessions.
- `deleteSessionInWishList`: Deletes a session from the user's wishlist using
the session's websafeKey.

#### 3. Two Additional Enhancement Queries

I have adde two additional queries for the API that can enhance the application more:

- `getTodaySessions`: Return all sessions happening today for the conferences
that the user has subscribed to. This will be helpful for users to get all
of the sessions happening today at a glance without the need to go through
many filters to get the data needed.
- `getLastChanceConferences`: Get all conferences with a last chance to attend.
This method will return all conferences with two numbers of seats or less. This
can help in displaying the conferences that the user should put an eye on
because seats available are running out.

#### Getting all sessions that are not workshops happening before 7 pm.

Queries are only allowed to have one inequality filter, and to get sessions
that are not workshops and happening before 7 pm I will need to use two filters
and that will cause a 'BadRequestException'. One solution for that is to do
one query that can use `ndb` filtering to get all sessions with time before 7pm.
Then I filter the results to check if each of those sessions contains the type
'workshop', and if not I will go then and add the session to a list representing
the filtered sessions. There's one draw-back with this method is that I have to
compare against a string, and that string can be 'Workshop', 'workshop' or even
'WORKSHOP'. I should check against all of those string cases and I feel that this
can be improved by using an Enum type for example instead of comparing against strings.

#### 4. Using Memcache and Adding Featured Speaker

- Inside `_createSessionObject` method a check is made to check if the speaker of this
session will give any other sessions under this conference. If so, we will add
a task to our created `taskqueue`, and this task will be responsible to
call the `SetFeaturedSpeakerHandler` request handler method in `main.py`
through this path: `/tasks/set_featured_speaker`.

- `SetFeaturedSpeakerHandler` request handler method calls the static method
`_cacheFeaturedSpeaker` that takes the speaker names, speaker sessions and the
conference name of this featured speaker as parameters. After that, we add the
cache data of this featued speaker to memcache using the key `FEATURED_SPEAKER_KEY`.

- To get the featured speaker, we use the method `getFeaturedSpeaker` under the
endpoint with name `getFeaturedSpeaker`.

- To be able to execute this task we add the following entry inside `app.yaml`:
  `- url: /tasks/set_featured_speaker
    script: main.app
    login: admin
  `
