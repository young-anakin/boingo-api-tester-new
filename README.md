# Boingo API Tester

A FastAPI application for testing Boingo API endpoints. This application provides a clean interface to interact with the Boingo API for scraping targets, results, agent status, and analytics.

## Features

- Authentication with Boingo API
- Management of scraping targets (create, read, update, delete)
- Handling of scraping results (create, read, update, delete)
- Agent status management
- Scraping analytics

## Installation

### Requirements

- Python 3.8+
- pip

### Setup

1. Clone the repository:
```bash
git clone https://github.com/YourUsername/boingo-api-tester.git
cd boingo-api-tester
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Create a `.env` file in the root directory with the following variables:
```
BOINGO_API_URL=https://api.boingo.com
BOINGO_EMAIL=your-email@example.com
BOINGO_PASSWORD=your-password
```

## Usage

1. Start the application:
```bash
uvicorn app.main:app --reload
```

2. Open your browser and navigate to [http://localhost:8000/docs](http://localhost:8000/docs) to access the Swagger UI documentation.

3. Use the interactive API documentation to test the various endpoints.

## API Endpoints

The application provides the following categories of endpoints:

- **/auth** - Authentication endpoints
- **/scraping-target** - Endpoints for managing scraping targets
- **/scraping-results** - Endpoints for managing scraping results
- **/agent-status** - Endpoints for managing agent status
- **/scraping-analytics** - Endpoints for accessing scraping analytics

## License

MIT

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request 