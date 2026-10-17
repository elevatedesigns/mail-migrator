# Google Workspace & Microsoft 365 setup

Mail Migrator talks to Google and Microsoft through their official APIs. To do
that, it needs an *app registration* with each company: a Google Cloud OAuth
client and a Microsoft Entra application.

- **Official builds** may already include registrations (Settings › Google &
  Microsoft apps tells you). If so, individual users can just click
  **Sign in with Google / Microsoft**.
- **Your own registrations** are needed when the official build doesn't include
  them, when your organization requires apps it controls, or for **admin
  mode** (migrating a whole organization without anyone's password).

| What you want | Google needs | Microsoft needs |
| --- | --- | --- |
| Users sign in themselves | OAuth client (Desktop app) | App registration (public client) |
| An admin migrates everyone | Service account + domain-wide delegation | App registration with application permissions + admin consent |

---

## Google Workspace

### 1. Create the project and turn on the APIs

1. Open the [Google Cloud console](https://console.cloud.google.com/) and create a project
   (for example "Mail Migrator").
2. **APIs & Services › Library**: enable **People API**, **Google Calendar API**,
   **Google Drive API** and, for admin mode, **Admin SDK API**.
   (Mail uses IMAP and needs no API.)
3. Make sure IMAP is on for your users: Google Admin console › Apps › Google
   Workspace › Gmail › End User Access › **POP and IMAP access**.

### 2. Sign in with Google (individual users)

1. **APIs & Services › OAuth consent screen**: choose **Internal** if only people
   in your Workspace will use it (no Google review needed). Choose **External** for
   anyone else; the mail and Drive permissions are *restricted*, so Google
   requires a verification and security assessment before external users beyond
   your test users can sign in.
2. Add these scopes: `https://mail.google.com/`,
   `https://www.googleapis.com/auth/contacts`,
   `https://www.googleapis.com/auth/calendar`,
   `https://www.googleapis.com/auth/drive`, plus `openid`, `email`, `profile`.
3. **Credentials › Create credentials › OAuth client ID** › Application type
   **Desktop app**. Copy the **client ID** and **client secret** into Mail
   Migrator's Settings › Google & Microsoft apps. (For desktop apps Google does
   not treat this secret as confidential.)

### 3. Admin mode (whole organization)

1. **IAM & Admin › Service accounts › Create service account**. No roles are
   needed. Open it › **Keys › Add key › JSON** and keep the file safe: it
   can act as any user you delegate below. (If key creation is blocked, an
   organization policy `iam.disableServiceAccountKeyCreation` is in force.)
2. Copy the service account's **Client ID** (a long number).
3. Google Admin console › Security › Access and data control › API controls ›
   **Manage domain-wide delegation › Add new**: paste the client ID and these
   scopes, comma separated:

   ```
   https://mail.google.com/,https://www.googleapis.com/auth/contacts,https://www.googleapis.com/auth/calendar,https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/admin.directory.user.readonly
   ```
4. In Mail Migrator choose **Workspace admin (whole organization)**, pick the
   JSON key, and enter a **super admin's email** (used to list users and shared
   drives). Changes to delegation can take a few minutes to apply.

---

## Microsoft 365

### 1. Register the app

1. [Microsoft Entra admin center](https://entra.microsoft.com/) › Identity ›
   Applications › **App registrations › New registration**.
2. **Supported account types**: "Accounts in any organizational directory and
   personal Microsoft accounts" for a build anyone can use, or "this
   organizational directory only" for your organization.
3. **Redirect URI**: platform **Public client/native (mobile & desktop)**,
   value `http://localhost`.
4. **Authentication** › Advanced settings › **Allow public client flows: Yes**.
5. Copy the **Application (client) ID**.

### 2. Sign in with Microsoft (individual users)

**API permissions › Add a permission**, then **Delegated permissions**:

- Microsoft Graph: `User.Read`, `Contacts.ReadWrite`, `Calendars.ReadWrite`,
  `Files.ReadWrite.All`, `Sites.ReadWrite.All`, `offline_access`
- APIs my organization uses › **Office 365 Exchange Online**:
  `IMAP.AccessAsUser.All`

Many tenants require an administrator to **Grant admin consent** for
`Files.ReadWrite.All` and `Sites.ReadWrite.All`. Enter the application ID in
Mail Migrator's Settings (tenant `common`, or your tenant ID to restrict it).

### 3. Admin mode (whole organization)

1. In the same app (or a separate one): **API permissions › Add a permission ›
   Application permissions**:
   - Microsoft Graph: `User.Read.All`, `Contacts.ReadWrite`,
     `Calendars.ReadWrite`, `Files.ReadWrite.All`, `Sites.FullControl.All`
     (copying sharing needs full control), and for shared drive migrations
     `Group.ReadWrite.All` and `GroupMember.ReadWrite.All`
   - Office 365 Exchange Online: `IMAP.AccessAsApp`
2. Click **Grant admin consent**.
3. **Certificates & secrets › New client secret**. Copy the secret *value*.
4. Allow the app to open mailboxes over IMAP (Exchange Online PowerShell):

   ```powershell
   Connect-ExchangeOnline
   # Use the Object ID from Enterprise applications (not App registrations)
   New-ServicePrincipal -AppId <application-id> -ObjectId <enterprise-app-object-id>
   # For each mailbox to migrate (or loop over Get-Mailbox):
   Add-MailboxPermission -Identity user@contoso.com -User <enterprise-app-object-id> -AccessRights FullAccess
   ```
   IMAP must also be enabled for the mailboxes (it is by default).
5. In Mail Migrator choose **Microsoft 365 admin (whole organization)** and enter
   the tenant (ID or `contoso.onmicrosoft.com`), application ID and client secret.

---

## Building releases with built-in app IDs

Official builds pick the IDs up from GitHub repository secrets (Settings ›
Secrets and variables › Actions), so they never appear in the source code:

| Secret | Value |
| --- | --- |
| `MM_GOOGLE_CLIENT_ID` | Google OAuth client ID (Desktop app) |
| `MM_GOOGLE_CLIENT_SECRET` | Its client secret |
| `MM_MICROSOFT_CLIENT_ID` | Microsoft application (client) ID |

Leave them unset to build without built-in IDs; users then enter their own in
Settings.

---

## What gets copied, and known limits

| Data | Copied | Not copied / notes |
| --- | --- | --- |
| Email | Every folder and message, flags, dates | Via imapsync; Gmail labels become folders |
| Contacts | Names, emails, phones, addresses, company, birthday, notes, websites, groups (↔ Outlook categories) | Contact photos. Outlook keeps at most 3 email addresses; extras go into notes |
| Calendars | Calendars you own; single and recurring events with changed and cancelled occurrences; reminders; private / free-busy status | Calendars shared with you by others. Events copied into Microsoft 365 list attendees in the notes unless you choose to add them (Outlook would invite them). Google never notifies attendees of copied events. Repeat rules Outlook can't express become individual events |
| Files | Folders and files with modified dates; Google Docs/Sheets/Slides → Word/Excel/PowerPoint (optionally back to Google formats) | Google Forms, Sites, My Maps, shortcuts; OneNote notebooks; files shared with you by others. Google limits the export of a native document to 10 MB |
| Sharing | People, groups, domain and anyone-with-the-link shares, without notifications | Owner changes; links to external people may be blocked by your sharing policy |
| Shared drives | Contents and members, to an existing or new SharePoint group site / Google shared drive | Site visitors (read-only group) of SharePoint sites |

Re-running a migration copies only new or changed items. What was copied is
remembered per mailbox pair in the app's data folder (`state/`).
