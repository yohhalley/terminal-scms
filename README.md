# MiniTOS WebApp

A web-based terminal operating system for managing container terminal operations.

## Architecture

### Database Schema

#### CHE (Container Handling Equipment)
- `id`: Unique identifier
- `name`: Equipment name
- `type`: Equipment type (e.g., RTG, RMG, STS)
- `status`: Current operational status
- `position`: Current position coordinates
- `fuel_level`: Current fuel level
- `last_maintenance`: Timestamp of last maintenance
- `assigned_tasks`: List of assigned tasks

#### Tasks
- `id`: Unique identifier
- `type`: Task type (e.g., pickup, dropoff)
- `status`: Current task status
- `priority`: Task priority level
- `assigned_che`: Reference to assigned CHE
- `start_time`: Task start timestamp
- `end_time`: Task end timestamp
- `location`: Task location coordinates

#### Containers
- `id`: Unique identifier
- `type`: Container type
- `status`: Current status
- `location`: Current location
- `assigned_task`: Reference to assigned task

## API Endpoints

### CHE Management
- `GET /api/che`: List all CHE units
- `GET /api/che/{id}`: Get specific CHE details
- `PUT /api/che/{id}`: Update CHE status/position
- `POST /api/che`: Add new CHE unit

### Task Management
- `GET /api/tasks`: List all tasks
- `GET /api/tasks/{id}`: Get specific task details
- `POST /api/tasks`: Create new task
- `PUT /api/tasks/{id}`: Update task status

### Container Management
- `GET /api/containers`: List all containers
- `GET /api/containers/{id}`: Get specific container details
- `PUT /api/containers/{id}`: Update container status/location

## Models

### CHE Model
```typescript
interface CHE {
    id: string;
    name: string;
    type: string;
    status: CHEStatus;
    position: Position;
    fuel_level: number;
    last_maintenance: Date;
    assigned_tasks: Task[];
}
```

### Task Model
```typescript
interface Task {
    id: string;
    type: TaskType;
    status: TaskStatus;
    priority: number;
    assigned_che: string;
    start_time: Date;
    end_time: Date;
    location: Position;
}
```

### Container Model
```typescript
interface Container {
    id: string;
    type: string;
    status: ContainerStatus;
    location: Position;
    assigned_task: string;
}
```

## WebApp Structure

### Frontend
- React-based single page application
- Real-time updates using WebSocket
- Responsive design for multiple devices
- Interactive map visualization
- Task management interface
- Equipment monitoring dashboard

### Backend
- Node.js/Express server
- RESTful API endpoints
- WebSocket server for real-time updates
- Authentication and authorization
- Database integration
- Task scheduling system

### Database
- MongoDB for flexible schema
- Real-time data synchronization
- Indexed queries for performance
- Data validation and integrity checks

## Development

### Prerequisites
- Node.js 16+
- MongoDB 4+
- npm or yarn

### Setup
1. Clone the repository
2. Install dependencies: `npm install`
3. Configure environment variables
4. Start development server: `npm run dev`

### Testing
- Unit tests: `npm test`
- Integration tests: `npm run test:integration`
- E2E tests: `npm run test:e2e`

## Deployment

### Production
1. Build the application: `npm run build`
2. Set up environment variables
3. Start the server: `npm start`

### Docker
1. Build the image: `docker build -t minitos .`
2. Run the container: `docker run -p 3000:3000 minitos`

## License

This project is licensed under the MIT License - see the LICENSE file for details. 