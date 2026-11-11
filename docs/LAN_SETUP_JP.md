# 別の PC から接続する

Job Server に別の PC から接続するための設定手順です。[English](LAN_SETUP.md)。
設計やセキュリティの詳細は [LAN_ACCESS.md](LAN_ACCESS.md) と [SECURITY.md](../SECURITY.md) を
参照してください。

例では次の値を使っています。実際の値に置き換えてください。

| 項目 | 例 |
|---|---|
| サーバー PC の LAN IP | `192.168.1.20` |
| HTTP ポート(既定) | `8766` |
| HTTPS ポート(既定) | `8767` |
| Tailscale の名前 | `your-pc.your-tailnet.ts.net` |

サーバー PC に Job Server 0.6.0 以上、接続元の PC に Job Manager 3.9.0 以上が必要です。
Job Server 0.5.0 は `127.0.0.1` でしか待ち受けません。

## 接続方法を選ぶ

| 接続方法 | URL | ファイアウォールのルール | 通信 | 向いている場面 |
|---|---|---|---|---|
| ローカル HTTPS(おすすめ) | `https://192.168.1.20:8767` | 必要 | 暗号化される | 両方の PC が同じ LAN にある |
| Tailscale | `https://your-pc.your-tailnet.ts.net` | 不要 | 暗号化される | 両方の PC が同じ tailnet にある、またはファイアウォールを変更できない |
| LAN HTTP | `http://192.168.1.20:8766` | 必要 | **暗号化されない** | 信頼できる LAN で、HTTPS を使えない |

スキームとポートは組み合わせを合わせてください。`8767` は HTTPS 専用なので、
`http://192.168.1.20:8767` では接続できません。

## ローカル HTTPS

### サーバー PC での設定

1. **Job Server > Settings** を開きます。
2. **Enable local HTTPS with a persistent local CA** を有効にします。
3. **Reachable from other PCs at** で、この PC の LAN IP にチェックを入れます。複数のネットワークに
   つながっている場合は、複数選べます。できる URL は **Connect using** に表示されます。
4. 保存すると再起動するか聞かれるので **Yes** を選びます。サーバーが止まっている場合は **Server > Start** です。
5. **Create / show CA certificate...** を開き、SHA-256 指紋を表示しておきます。
6. LAN IP で待ち受けていることを確認します。手順 6〜9 は Windows 用です。Linux と Mac は
   [サーバーが Linux または Mac の場合](#サーバーが-linux-または-mac-の場合) を参照してください。

   ```powershell
   Get-NetTCPConnection -State Listen -LocalPort 8766,8767 | Select-Object LocalAddress,LocalPort
   ```

   一覧に `192.168.1.20` が必要です。`127.0.0.1` だけなら、手順 2〜4 を確認してください。

7. **管理者**の PowerShell を開き、ネットワークプロファイルを確認します。

   ```powershell
   Get-NetConnectionProfile | Select-Object InterfaceAlias,NetworkCategory
   ```

8. LAN のアダプターが `Public` で、信頼できるネットワークなら `Private` に変更します。
   アダプター名は手順 7 で表示された名前にしてください。

   ```powershell
   Set-NetConnectionProfile -InterfaceAlias "イーサネット" -NetworkCategory Private
   ```

9. ポートを許可します。プロファイルを変更しただけでは開きません。

   ```powershell
   New-NetFirewallRule -Name "MoleditPy-Job-Server-LAN-HTTPS" -DisplayName "Job Server LAN HTTPS" -Direction Inbound -Action Allow -Protocol TCP -LocalAddress 192.168.1.20 -LocalPort 8767 -RemoteAddress LocalSubnet -Profile Private
   ```

### サーバーが Linux または Mac の場合

上の手順 6〜9 は Windows 用です。Linux と Mac では、代わりに次を実行します。どのルールも再起動後に
残ります。ファイアウォールが動いていなければ、ルールは不要です。

LAN IP で待ち受けていることを確認します。

```bash
# Linux
ss -ltn | grep -E ':(8766|8767)'
# Mac
lsof -nP -iTCP -sTCP:LISTEN | grep -E ':(8766|8767)'
```

**Linux の ufw**(Ubuntu、Debian)。先に `sudo ufw status` で状態を確認してください。
`192.168.1.0/24` は実際のサブネットに置き換えます。

```bash
sudo ufw allow from 192.168.1.0/24 to 192.168.1.20 port 8767 proto tcp
```

**Linux の firewalld**(Fedora、RHEL)。先に `sudo firewall-cmd --state` で状態を確認してください。
`public` は `sudo firewall-cmd --get-active-zones` で表示されるゾーンに置き換えます。

```bash
sudo firewall-cmd --permanent --zone=public --add-rich-rule='rule family="ipv4" source address="192.168.1.0/24" destination address="192.168.1.20" port port="8767" protocol="tcp" accept'
sudo firewall-cmd --reload
```

**Mac。** 標準のファイアウォールはポートではなくアプリ単位で許可するので、接続元をサブネットに
絞れません。先に状態を確認します。

```bash
/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate
```

オンになっていれば、Job Server を動かしている Python を許可します。パスは、その環境で
`python3 -c "import os, sys; print(os.path.realpath(sys.executable))"` を実行した結果に
置き換えます。

```bash
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add /path/to/python3
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp /path/to/python3
```

CI でテストしているサーバーは Windows と Linux で、Mac のサーバーはテストしていません。

### 接続元の PC での設定

1. ポートに届くことを確認します。`-k` は証明書の検証を省略するので、認証情報を送らない
   この確認にだけ使ってください。

   ```powershell
   curl.exe -k https://192.168.1.20:8767/api/v1/health
   ```

   Linux と Mac では `curl.exe` の代わりに `curl` を使います。

   応答に `"status":"ok"` が含まれていれば接続できています。

2. Job Manager のホスト設定を開き、**Backend** を **Job Server (HTTPS)** にします。
3. **Server URL** に `https://192.168.1.20:8767` を入れます。
4. **Trust local server CA...** を選びます。表示された指紋をサーバー PC の指紋と見比べて、
   一致しているときだけ **Yes** を選びます。
5. **Sign in to Job Server...** を選び、ユーザー名とパスワードを入れます。

## ローカル HTTPS で Web UI を使う

PC がサーバーの CA を信頼するまで、`https://192.168.1.20:8767` を開くとブラウザーに証明書の警告が
出ます。Web UI を開く PC ごとに設定してください。公開された正規の証明書を使う Tailscale と、
証明書を使わない LAN HTTP では不要です。

この証明書を入れると、その PC は、この CA がプライベートアドレスと localhost 向けに署名したものを
すべて信頼します。サーバー PC と、そこでジョブを実行できる全員を信頼できるときだけ入れてください。
詳しくは [SECURITY.md](../SECURITY.md) を参照してください。

### 1. 証明書を入手する

サーバー PC で **Job Server > Settings > Create / show CA certificate...** を開き、
**Export public CA certificate...** を選んで、`job-server-ca.pem` を別の PC にコピーします。

別の PC で `https://192.168.1.20:8767/api/v1/tls/ca` からダウンロードすることもできます。
このときはブラウザーの警告をこの 1 回だけ進むことになるので、手順 2 が特に重要です。

### 2. 指紋を確認する

結果をサーバー PC の SHA-256 指紋と見比べます。大文字と小文字、コロンの違いは無視してかまいません。

- Windows: 出力の**最後の** `Cert ハッシュ(sha256)` の行を見ます。

  ```powershell
  certutil -dump job-server-ca.pem
  ```

- Mac と Linux:

  ```bash
  openssl x509 -in job-server-ca.pem -noout -fingerprint -sha256
  ```

### 3. インストールする

| ブラウザーと OS | インストール方法 |
|---|---|
| Windows の Edge / Chrome | `certutil -user -addstore Root job-server-ca.pem` を実行し、Windows の確認を承諾する。同じ Windows ユーザーで Job Manager の **Trust local server CA...** が成功していれば不要 |
| Mac の Safari / Chrome | `security add-trusted-cert -r trustRoot -k ~/Library/Keychains/login.keychain-db job-server-ca.pem` を実行する |
| Linux の Chrome | `certutil -d sql:$HOME/.pki/nssdb -A -t "C,," -n "MoleditPy Job Server Local CA" -i job-server-ca.pem` を実行する(`libnss3-tools` に含まれる) |
| すべての OS の Firefox | **設定 > プライバシーとセキュリティ > 証明書 > 証明書を表示 > 認証局証明書 > インポート** で読み込み、**この認証局によるウェブサイトの識別を信頼する** を選ぶ |
| iPhone / iPad | ファイルを開いて **設定** でプロファイルをインストールし、**一般 > 情報 > 証明書信頼設定** で有効にする |
| Android | **設定 > セキュリティ** から **CA 証明書** としてインストールする。メニューの場所は機種によって異なる |

### 4. Web UI を開く

ブラウザーを再起動して `https://192.168.1.20:8767` を開きます。サーバー PC の IP が変わっても CA は
同じなので、変わるのは URL だけです。

Windows で信頼を取り消すには、`certmgr.msc` を開き、**信頼されたルート証明機関** にある
**MoleditPy Job Server Local CA** を削除します。

## CA を作り直す

CA の鍵が漏れた可能性があるときは、**Job Server > Settings > Regenerate CA certificate...** を選んで、
サーバーを再起動します。すべての PC とブラウザーで、前述の手順で新しい CA を信頼し、古い CA を削除して
ください。古いファイルはデータディレクトリの `tls` フォルダーにある `retired-` フォルダーへ移動します。

## .local 名で接続する

`https://your-pc.local:8767` なら、サーバー PC の IP が変わっても同じ URL で接続できます。Job Server 0.6.2
以上と、`.local` 名に対応した Job Manager が必要です。**Allow trusted LAN HTTP** が有効なら、暗号化なしの
`http://your-pc.local:8766` も使えます。`.local` 名は LAN 内のどの機器でも名乗れるので、HTTP は機器を信頼
できる LAN でだけ使ってください。

1. 別の PC から名前を解決できるか確認します: `ping your-pc.local`。Windows、Mac、Avahi の入った Linux は
   PC 名を通知します。ゲストネットワークや管理されたネットワークでは遮断されることがあります。
2. サーバー PC の **Reachable from other PCs at** で `your-pc.local` にもチェックを入れます。LAN IP の
   チェックは外さないでください。サーバーは IP アドレスで待ち受け、名前はそのどれかに解決されます。
3. 保存して再起動します。CA はこの 1 つの名前だけに限定されるので、この名前なしで作った CA や別の名前の
   CA は置き換えが必要で、Settings が先に確認します。置き換えると、
   すべての PC とブラウザーで、前述の手順で新しい CA を信頼し、古い CA を削除してください。古いファイルは
   データディレクトリの `tls` フォルダーにある `retired-` フォルダーへ移動します。
4. Job Manager で **Server URL** を `https://your-pc.local:8767` にして、**Trust local server CA...** を
   選びます。

## Tailscale

両方の PC が同じ tailnet にサインインしている必要があります。使うのは非公開の Tailscale Serve
だけにしてください。Funnel やルーターのポート転送は使わないでください。

1. サーバー PC で次を実行し、表示された URL を控えます。

   ```powershell
   tailscale serve --bg http://127.0.0.1:8766
   ```

2. その URL を **Job Server > Settings > Other web URLs** に追加し、保存して再起動します。
3. Job Manager の **Server URL** に `https://your-pc.your-tailnet.ts.net` を入れます。
4. **Sign in to Job Server...** を選びます。公開された正規の証明書を使うので、
   **Trust local server CA...** は不要です。

## LAN HTTP

パスワード、トークン、ジョブの内容は、LAN の通信を傍受できる人に読まれます。信頼できる LAN でだけ
使ってください。

1. サーバー PC で **Allow trusted LAN HTTP** を有効にし、**Reachable from other PCs at** でアドレスに
   チェックを入れて、
   保存して再起動します。
2. ローカル HTTPS の手順 7〜9、または [サーバーが Linux または Mac の場合](#サーバーが-linux-または-mac-の場合) を、
   ポート `8766` で実行します。Windows ではルール名も別にします。
3. Job Manager の **Server URL** に `http://192.168.1.20:8766` を入れます。
4. **Allow trusted LAN HTTP (unencrypted)** を有効にします。
5. **Sign in to Job Server...** を選びます。

## トラブルシューティング

| 症状 | 考えられる原因 | 対処 |
|---|---|---|
| ping は通るがポートがタイムアウトする | サーバー PC のファイアウォール、または LAN で待ち受けていない | 待ち受けを確認してから、ファイアウォールのルールを追加する |
| プロファイルを `Private` にしてもタイムアウトする | そのポートのルールがない | ルールを追加する。プロファイルの変更だけでは開かない |
| ルールがあるのにタイムアウトする | ルールのプロファイルが合っていない、グループポリシー、別のセキュリティソフト | ルールとプロファイルを確認し、管理者に相談する |
| `127.0.0.1` でしか待ち受けていない | LAN の待ち受けがオフ、またはサーバーが 0.6.0 より古い | Settings で有効にして再起動するか、更新する |
| `Set-NetConnectionProfile` が権限エラーになる | 管理者でない、ドメインネットワーク、グループポリシー | 管理者で再実行する。それでも失敗するなら管理者に相談するか Tailscale を使う |
| `8767` への `http://` が失敗する | このポートは HTTPS 専用 | `https://` を使うか、`http://` ならポート `8766` を使う |
| 証明書エラーになる | CA が信頼されていない、または IP が変わった | **Trust local server CA...** をやり直す。IP が変わったら URL も更新する |
| ブラウザーで `origin_forbidden` になる | そのアドレスが登録されていない | Settings でチェックを入れるか、正確な origin を **Other web URLs** に追加して、再起動する |
| `.local` 名を解決できない | ネットワークがマルチキャストを遮断している、または PC が名前を通知していない | IP アドレスを使う |
| `.local` 名は解決できるがタイムアウトする | 解決先のアドレスにチェックが入っていない、またはファイアウォール | そのアドレスにチェックを入れるか、ファイアウォールのルールを追加する |

サーバー PC を組織が管理している場合は、ネットワークプロファイルやファイアウォールのルールを変更する前に
管理者に確認してください。
