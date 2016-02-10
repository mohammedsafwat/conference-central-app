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

- Speakers were defined as a string. I didn't want to complicate the relationships
at this point by creating a separate entity for each speaker. In future, I'll do that
because I'll add more features to query all of the speakers and get more info about
each one like the speaker's name, current job, photo, etc.

- Types of session are represented as an array of strings, each string holds a
value for a separate session type.

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

For finding sessions that do not have 'Workshop' type and that happen before 7pm,
I used `ndb` filtering to get all sessions with time before 7pm. Then I checked
if each of those sessions contains the type 'workshop', and if not I will go
then and add the session to a list representing the filtered sessions. There's
one draw-back with this method is that I have to compare against a string, and
that string can be 'Workshop', 'workshop' or even 'WORKSHOP'. I should
check against all of those string cases and I feel that this can be improved by
using an Enum type for example instead of comparing against strings.

#### 4. Using Memcache and Adding Featured Speaker

Inside `createSession` method a check is made to see of the speaker is added
to any other session across all conferences. If so, the speaker name and relevant
session names are added to the memcache under the `featured_speaker` key.

- `getFeaturedSpeaker` endpoint is used to check for the featured speaker. The
check is done inside memcache with the key `featured_speaker`. If the result
is empty, the next upcoming speaker data is pulled and returned.
