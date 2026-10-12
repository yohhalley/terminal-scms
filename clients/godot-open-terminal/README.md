# Godot Open Terminal

A Godot-based terminal interface for managing and monitoring industrial operations.

## Features

### MQTT Integration
- Connects to MQTT broker for real-time data exchange
- Implements SparkPlugB protocol for device communication
- Supports device birth, data, and death messages
- Handles MQTT connection errors and reconnection
- Uses QoS levels for reliable message delivery

### HTTP Request Management
- Manages HTTP requests to external APIs
- Handles authentication and session management
- Supports both GET and POST requests
- Implements error handling and retry mechanisms
- Processes JSON responses

### MiniTOS REST API Integration
- Connects to MiniTOS backend for data synchronization
- Retrieves CHE (Container Handling Equipment) data
- Manages unit status and position updates
- Handles authentication and session management
- Processes real-time updates from MiniTOS

### Ignition Gateway Connection
- Connects to Ignition Gateway for SCADA integration
- Implements SparkPlugB protocol for device communication
- Manages device state and data updates
- Handles connection errors and reconnection
- Supports real-time data visualization

## Architecture

### MQTT Manager
- Handles MQTT broker connection
- Manages topics and subscriptions
- Implements SparkPlugB message formatting
- Handles connection state and errors
- Provides utility functions for MQTT operations

### HTTP Request Manager
- Manages HTTP client connections
- Handles request queuing and processing
- Implements authentication and session management
- Processes response data
- Manages error handling and retries

### MiniTOS Integration
- Connects to MiniTOS REST API
- Manages CHE data synchronization
- Handles unit status updates
- Processes position and state changes
- Implements error handling and recovery

### Ignition Gateway Integration
- Connects to Ignition Gateway
- Implements SparkPlugB protocol
- Manages device state and data
- Handles real-time updates
- Provides error handling and recovery

## Setup

1. Configure MQTT broker settings in `MqttManager.gd`
2. Set up HTTP request parameters in `HTTPRequestManager.gd`
3. Configure MiniTOS API endpoints in `HTTPRequestManager.gd`
4. Set up Ignition Gateway connection in `MqttManager.gd`

## Usage

1. Start the application
2. MQTT connection is established automatically
3. HTTP requests are managed through the HTTP Request Manager
4. MiniTOS data is synchronized automatically
5. Ignition Gateway connection is established automatically

## Error Handling

- MQTT connection errors are handled with automatic reconnection
- HTTP request errors are managed with retry mechanisms
- MiniTOS API errors are handled with appropriate error messages
- Ignition Gateway connection errors are managed with reconnection attempts

## Dependencies

- Godot 4.x
- MQTT Client
- HTTP Client
- JSON Parser

## License

This project is licensed under the MIT License - see the LICENSE file for details.
