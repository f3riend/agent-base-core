from fastapi import FastAPI, Header, HTTPException
from uuid import uuid4

app = FastAPI()

# Fake database
users = {
    "oktay": {
        "api_key": str(uuid4()),

        "stores": [
            {
                "id": 1,
                "name": "Sepetler Market",

                "campaigns": [
                    {
                        "id": 101,
                        "product": "Coca Cola",
                        "published": False,

                        "pricing": {
                            "old_price": 30,
                            "new_price": 20,
                            "discount_percent": 33
                        },

                        "campaign_dates": {
                            "start_date": "2026-05-13",
                            "end_date": "2026-06-01"
                        },

                        "media": [
                            "https://example.com/banner1.png",
                            "https://example.com/banner2.png"
                        ],

                        "redirect_url": "https://example.com/product/cocacola"
                    }
                ]
            },

            {
                "id": 2,
                "name": "Teknoloji Shop",

                "campaigns": [
                    {
                        "id": 201,
                        "product": "Kulaklık",
                        "published": False,

                        "pricing": {
                            "old_price": 1000,
                            "new_price": 750,
                            "discount_percent": 25
                        },

                        "campaign_dates": {
                            "start_date": "2026-05-15",
                            "end_date": "2026-05-30"
                        },

                        "media": [
                            "https://example.com/headphone.png"
                        ],

                        "redirect_url": "https://example.com/product/headphone"
                    }
                ]
            }
        ]
    }
}


# API key ile kullanıcı bul
def get_user_by_api_key(api_key):

    for username, data in users.items():

        if data["api_key"] == api_key:
            return username, data

    return None, None


# API keyleri göster
@app.get("/keys")
def get_keys():

    return {
        username: data["api_key"]
        for username, data in users.items()
    }


# Kullanıcının mağazalarını listele
@app.get("/stores")
def get_stores(x_api_key: str = Header(None)):

    username, user = get_user_by_api_key(x_api_key)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid API Key"
        )

    return {
        "user": username,
        "stores": user["stores"]
    }


# Tek mağaza getir
@app.get("/stores/{store_id}")
def get_store(
    store_id: int,
    x_api_key: str = Header(None)
):

    username, user = get_user_by_api_key(x_api_key)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid API Key"
        )

    for store in user["stores"]:

        if store["id"] == store_id:

            return {
                "user": username,
                "store": store
            }

    raise HTTPException(
        status_code=404,
        detail="Store not found"
    )

@app.get("/stores/{store_id}")
def get_store(
    store_id: int,
    x_api_key: str = Header(None)
):

    username, user = get_user_by_api_key(x_api_key)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid API Key"
        )

    for store in user["stores"]:

        if store["id"] == store_id:

            return {
                "user": username,
                "store": store
            }

    raise HTTPException(
        status_code=404,
        detail="Store not found"
    )


# Campaign publish endpointi
@app.post("/stores/{store_id}/campaigns/{campaign_id}/publish")
def publish_campaign(
    store_id: int,
    campaign_id: int,
    x_api_key: str = Header(None)
):

    username, user = get_user_by_api_key(x_api_key)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid API Key"
        )

    for store in user["stores"]:

        if store["id"] == store_id:

            for campaign in store["campaigns"]:

                if campaign["id"] == campaign_id:

                    campaign["published"] = True

                    return {
                        "message": "Campaign published successfully",
                        "user": username,
                        "store": store["name"],
                        "campaign": campaign
                    }

    raise HTTPException(
        status_code=404,
        detail="Campaign not found"
    )