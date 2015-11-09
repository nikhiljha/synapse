# -*- coding: utf-8 -*-
# Copyright 2015 OpenMarket Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from twisted.internet import defer

from _base import SQLBaseStore
from synapse.api.errors import SynapseError
from synapse.storage.engines import PostgresEngine, Sqlite3Engine

import logging


logger = logging.getLogger(__name__)


class SearchStore(SQLBaseStore):
    @defer.inlineCallbacks
    def search_msgs(self, room_ids, search_term, keys):
        """Performs a full text search over events with given keys.

        Args:
            room_ids (list): List of room ids to search in
            search_term (str): Search term to search for
            keys (list): List of keys to search in, currently supports
                "content.body", "content.name", "content.topic"

        Returns:
            list of dicts
        """
        clauses = []
        args = []

        # Make sure we don't explode because the person is in too many rooms.
        # We filter the results below regardless.
        if len(room_ids) < 500:
            clauses.append(
                "room_id IN (%s)" % (",".join(["?"] * len(room_ids)),)
            )
            args.extend(room_ids)

        local_clauses = []
        for key in keys:
            local_clauses.append("key = ?")
            args.append(key)

        clauses.append(
            "(%s)" % (" OR ".join(local_clauses),)
        )

        if isinstance(self.database_engine, PostgresEngine):
            sql = (
                "SELECT ts_rank_cd(vector, query) AS rank, room_id, event_id"
                " FROM plainto_tsquery('english', ?) as query, event_search"
                " WHERE vector @@ query"
            )
        elif isinstance(self.database_engine, Sqlite3Engine):
            sql = (
                "SELECT rank(matchinfo(event_search)) as rank, room_id, event_id"
                " FROM event_search"
                " WHERE value MATCH ?"
            )
        else:
            # This should be unreachable.
            raise Exception("Unrecognized database engine")

        for clause in clauses:
            sql += " AND " + clause

        # We add an arbitrary limit here to ensure we don't try to pull the
        # entire table from the database.
        sql += " ORDER BY rank DESC LIMIT 500"

        results = yield self._execute(
            "search_msgs", self.cursor_to_dict, sql, *([search_term] + args)
        )

        results = filter(lambda row: row["room_id"] in room_ids, results)

        events = yield self._get_events([r["event_id"] for r in results])

        event_map = {
            ev.event_id: ev
            for ev in events
        }

        defer.returnValue([
            {
                "event": event_map[r["event_id"]],
                "rank": r["rank"],
            }
            for r in results
            if r["event_id"] in event_map
        ])

    @defer.inlineCallbacks
    def search_room(self, room_id, search_term, keys, limit, pagination_token=None):
        """Performs a full text search over events with given keys.

        Args:
            room_id (str): The room_id to search in
            search_term (str): Search term to search for
            keys (list): List of keys to search in, currently supports
                "content.body", "content.name", "content.topic"
            pagination_token (str): A pagination token previously returned

        Returns:
            list of dicts
        """
        clauses = []
        args = [search_term, room_id]

        local_clauses = []
        for key in keys:
            local_clauses.append("key = ?")
            args.append(key)

        clauses.append(
            "(%s)" % (" OR ".join(local_clauses),)
        )

        if pagination_token:
            try:
                topo, stream = pagination_token.split(",")
                topo = int(topo)
                stream = int(stream)
            except:
                raise SynapseError(400, "Invalid pagination token")

            clauses.append(
                "(topological_ordering < ?"
                " OR (topological_ordering = ? AND stream_ordering < ?))"
            )
            args.extend([topo, topo, stream])

        if isinstance(self.database_engine, PostgresEngine):
            sql = (
                "SELECT ts_rank_cd(vector, query) as rank,"
                " topological_ordering, stream_ordering, room_id, event_id"
                " FROM plainto_tsquery('english', ?) as query, event_search"
                " NATURAL JOIN events"
                " WHERE vector @@ query AND room_id = ?"
            )
        elif isinstance(self.database_engine, Sqlite3Engine):
            sql = (
                "SELECT rank(matchinfo(event_search)) as rank, room_id, event_id,"
                " topological_ordering, stream_ordering"
                " FROM event_search"
                " NATURAL JOIN events"
                " WHERE value MATCH ? AND room_id = ?"
            )
        else:
            # This should be unreachable.
            raise Exception("Unrecognized database engine")

        for clause in clauses:
            sql += " AND " + clause

        # We add an arbitrary limit here to ensure we don't try to pull the
        # entire table from the database.
        sql += " ORDER BY topological_ordering DESC, stream_ordering DESC LIMIT ?"

        args.append(limit)

        results = yield self._execute(
            "search_rooms", self.cursor_to_dict, sql, *args
        )

        events = yield self._get_events([r["event_id"] for r in results])

        event_map = {
            ev.event_id: ev
            for ev in events
        }

        defer.returnValue([
            {
                "event": event_map[r["event_id"]],
                "rank": r["rank"],
                "pagination_token": "%s,%s" % (
                    r["topological_ordering"], r["stream_ordering"]
                ),
            }
            for r in results
            if r["event_id"] in event_map
        ])
