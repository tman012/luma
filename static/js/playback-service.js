/**
 * Playback Service
 * Manages Plex playback across different rooms/devices
 *
 * IMPORTANT: Requires window.API_KEY to be set before loading this script
 *
 * Rooms are fetched dynamically from /api/settings/rooms on initialization.
 */

(function() {
    'use strict';

    /**
     * Normalise a room object coming from the settings API so it always
     * has consistent fields for legacy callers.
     *
     * The backend now returns a nested structure:
     *   Room → devices[] → services[]
     *
     * We flatten this by taking the first device and its first service,
     * producing the flat shape that all existing legacy templates expect.
     */
    function normaliseRoom(room) {
        // Prefer device with a Plex service (this is a Plex playback service)
        var device = {};
        if (Array.isArray(room.devices) && room.devices.length > 0) {
            for (var i = 0; i < room.devices.length; i++) {
                var d = room.devices[i];
                if (Array.isArray(d.services)) {
                    for (var j = 0; j < d.services.length; j++) {
                        if (d.services[j].type === 'plex') {
                            device = d;
                            break;
                        }
                    }
                }
                if (device.host) break;
            }
            // Fall back to first device if none has Plex
            if (!device.host) device = room.devices[0];
        }
        // Prefer Plex service on the selected device
        var service = {};
        if (Array.isArray(device.services) && device.services.length > 0) {
            for (var k = 0; k < device.services.length; k++) {
                if (device.services[k].type === 'plex') {
                    service = device.services[k];
                    break;
                }
            }
            if (!service.id) service = device.services[0];
        }

        var plexPort = (service.type === 'plex' && service.plex_client_port)
            ? service.plex_client_port
            : 32500;
        var plexMachineId = (service.type === 'plex' && service.plex_machine_id)
            ? service.plex_machine_id
            : '';

        var flat = {
            id:               room.id || '',
            name:             room.name || '',
            host:             device.host || '',
            ip:               device.host || '',
            platform:         device.platform || 'android_tv',
            connectPort:      device.connectPort || 5555,
            adb_port:         device.connectPort || 5555,
            pairPort:         device.pairPort || null,
            paired:           !!device.paired,
            port:             plexPort,
            plex_client_port: plexPort,
            plex_machine_id:  plexMachineId,
            device_id:        device.id || '',
            service_id:       service.id || ''
        };

        // Default icon based on room name
        var lower = flat.name.toLowerCase();
        if (lower.indexOf('bed') !== -1) flat.icon = 'fas fa-bed';
        else if (lower.indexOf('theater') !== -1 || lower.indexOf('theatre') !== -1) flat.icon = 'fas fa-film';
        else if (lower.indexOf('office') !== -1) flat.icon = 'fas fa-laptop';
        else flat.icon = 'fas fa-tv';

        return flat;
    }

    class PlaybackService {
        constructor() {
            this.rooms = [];
            this.defaultRoomId = '';
            this.roomsLoaded = false;
            this.plexToken = window.API_KEY || '';

            if (!this.plexToken) {
                console.warn('[PlaybackService] No API_KEY found. Playback may not work.');
            }

            // Kick off async room fetch
            this._fetchRooms();
        }

        /**
         * Fetch rooms from the settings API.
         */
        async _fetchRooms() {
            try {
                var response = await fetch('/api/settings/rooms');
                if (response.ok) {
                    var data = await response.json();
                    var rooms = data.rooms;
                    this.defaultRoomId = data.defaultRoomId || (data.defaultPlaybackTarget && data.defaultPlaybackTarget.roomId) || '';
                    if (Array.isArray(rooms) && rooms.length > 0) {
                        this.rooms = rooms.map(normaliseRoom);
                        this.roomsLoaded = true;
                        console.log('[PlaybackService] Loaded', this.rooms.length, 'rooms from settings API');
                    } else {
                        console.warn('[PlaybackService] Settings API returned empty rooms');
                    }
                } else {
                    console.warn('[PlaybackService] Failed to fetch rooms (HTTP', response.status, ')');
                }
            } catch (error) {
                console.warn('[PlaybackService] Error fetching rooms:', error);
            }
        }

        /**
         * Get all configured rooms
         * @returns {Array} Array of room objects
         */
        getAllRooms() {
            return this.rooms;
        }

        /**
         * Get a specific room by ID
         * @param {string} roomId - The room ID
         * @returns {Object|null} Room object or null if not found
         */
        getRoom(roomId) {
            return this.rooms.find(function(room) { return room.id === roomId; });
        }

        /**
         * Get the default room
         * @returns {Object|null}
         */
        getDefaultRoom() {
            if (this.defaultRoomId) {
                var room = this.getRoom(this.defaultRoomId);
                if (room) return room;
            }
            return this.rooms.length > 0 ? this.rooms[0] : null;
        }

        /**
         * Get resume information for a rating key
         * @param {string} ratingKey - The Plex rating key for the content
         * @returns {Promise} Resolves with resume information
         */
        async getResumeInfo(ratingKey) {
            try {
                const response = await fetch(`/api/plex/resume_info/${ratingKey}`);
                if (!response.ok) {
                    throw new Error('Failed to fetch resume info');
                }
                return await response.json();
            } catch (error) {
                console.error('[PlaybackService] Error fetching resume info:', error);
                return {
                    has_resume_point: false,
                    offset_ms: 0,
                    offset_seconds: 0,
                    time_string: '0:00',
                    rating_key: ratingKey
                };
            }
        }

        /**
         * Play content in a specific room
         * @param {string} roomId - The room ID
         * @param {string} ratingKey - The Plex rating key for the content
         * @param {number} resumeOffset - Optional resume offset in milliseconds (default: 0)
         * @returns {Promise} Resolves when playback is initiated
         */
        async playInRoom(roomId, ratingKey, resumeOffset = 0) {
            const room = this.getRoom(roomId);

            if (!room) {
                throw new Error(`Room not found: ${roomId}`);
            }

            if (!room.host) {
                throw new Error(`Room ${room.name} is not configured. Please set the host/IP address.`);
            }

            console.log(`[PlaybackService] Starting playback in ${room.name}...`);
            console.log(`[PlaybackService] Rating Key: ${ratingKey}`);
            console.log(`[PlaybackService] Resume Offset: ${resumeOffset}ms`);
            console.log(`[PlaybackService] Target: ${room.host}:${room.port}`);

            try {
                const payload = {
                    rating_key: ratingKey,
                    client_ip: room.host,
                    client_port: room.port,
                    room_id: roomId,
                    resume_offset: resumeOffset,
                    device_id: room.device_id || '',
                    service_id: room.service_id || ''
                };

                console.log('[PlaybackService] Making API call to /api/player/play');

                return new Promise((resolve, reject) => {
                    const xhr = new XMLHttpRequest();
                    xhr.open('POST', '/api/player/play', true);
                    xhr.setRequestHeader('Content-Type', 'application/json');

                    xhr.onload = function() {
                        if (xhr.status >= 200 && xhr.status < 300) {
                            try {
                                const result = JSON.parse(xhr.responseText);
                                console.log('[PlaybackService] Playback started successfully:', result);
                                resolve(result);
                            } catch (e) {
                                reject(e);
                            }
                        } else {
                            reject(new Error('Failed to start playback'));
                        }
                    };

                    xhr.onerror = function() {
                        reject(new Error('Network error'));
                    };

                    xhr.send(JSON.stringify(payload));
                });

            } catch (error) {
                console.error('[PlaybackService] Playback error:', error);
                throw error;
            }
        }

        /**
         * Update room configuration
         * @param {string} roomId - The room ID
         * @param {Object} config - Room configuration updates
         */
        updateRoom(roomId, config) {
            const room = this.getRoom(roomId);
            if (room) {
                Object.assign(room, config);
                console.log(`[PlaybackService] Updated room ${roomId}:`, room);
            }
        }
    }

    // Initialize and expose globally
    window.playbackService = new PlaybackService();
    console.log('[PlaybackService] Initialized (rooms loading from API)');
})();
