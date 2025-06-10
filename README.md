Functional Requirements
1.	The user can view a list of available books.
2.	The user can borrow a book (loan).
3.	The user can return a borrowed book.
4.	The user can reserve a book (reservation).
5.	The system tracks which books are currently loaned or available.
6.	The user receives notifications:

Non-Functional Requirements
| Requirement         | Description                                        |
| ------------------- | -------------------------------------------------- |
| **Scalability**     | Must support growth in users, books, and loans     |
| **Availability**    | Should be available 24/7                           |
| **Performance**     | Core ops respond in < 1s                           |
| **Security**        | Encrypt all PII (names, emails) in transit/storage |
| **Logging**         | Log all user/system events                         |
| **Monitoring**      | `/health` and `/metrics` endpoints required        |
| **CI/CD**           | Full pipeline with build, test, deploy             |
| **Fault Tolerance** | Graceful failure handling and retries              |
| **Consistency**     | Book status must remain accurate                   |
| **Usability**       | Simple, user-friendly API for users/admins         |



Database Description

![image](https://github.com/user-attachments/assets/d0bb3d61-b786-4edf-8b0e-36564e98a3ec)


This database is designed for a simple library system that manages users, books, and book loans. It consists of three tables: users, books, and loans.
Table: users
Stores information about users registered in the system.
| Field          | Type        | Description                                                     |
| -------------- | ----------- | --------------------------------------------------------------- |
| `id`           | serial (PK) | Unique identifier for the user                                  |
| `name`         | text        | User's name                                                     |
| `email`        | text        | User's email address                                            |
| `created_date` | timestamp   | Date and time the user was created (default: current timestamp) |

Table: books
Contains information about the books available in the library.
| Field          | Type        | Description                                                   |
| -------------- | ----------- | ------------------------------------------------------------- |
| `id`           | serial (PK) | Unique identifier for the book                                |
| `name`         | text        | Title of the book                                             |
| `author`       | text        | Author of the book                                            |
| `available`    | boolean     | Availability status of the book (default: true)               |
| `created_date` | timestamp   | Date and time the book was added (default: current timestamp) |

Table: loans
Tracks the lending of books to users.
| Field       | Type        | Description                                                                      |
| ----------- | ----------- | -------------------------------------------------------------------------------- |
| `id`        | serial (PK) | Unique identifier for the loan record                                            |
| `user_id`   | int         | Foreign key referencing `users.id`, identifies the user who borrowed a book      |
| `book_id`   | int         | Foreign key referencing `books.id`, identifies the borrowed book                 |
| `loan_date` | timestamp   | Date and time the loan was made (default: current timestamp)                     |
| `due_date`  | timestamp   | Date and time the book is due to be returned (e.g., current timestamp + 14 days) |

Services communication diagram
![image](https://github.com/user-attachments/assets/74808d80-7dcf-45fd-ae63-4fe63703bd70)

This system follows a microservices architecture with the following components:

### API Gateway
- Routes client requests to appropriate services.
- Acts as a unified entry point.

### User Service
- Manages user data.
- Communicates with Book Service for user-specific queries.

### Book Service
- Handles book catalog and availability.
- Talks to:
  - User Service (sync)
  - Loan Service (async message processing)

### Loan Service
- Manages book loans and due dates.
- Sends messages to Book Service to update availability.

### Communication Summary

| Source        | Target        | Type         | Description                        |
|---------------|---------------|--------------|------------------------------------|
| API Gateway   | All Services  | HTTP (sync)  | Main request routing               |
| Book Service  | User Service  | HTTP (sync)  | Fetch user info                    |
| Loan Service  | Book Service  | Messaging    | Update book availability on loan   |



